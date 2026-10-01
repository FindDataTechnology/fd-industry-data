"""DBnomics aggregation API spider (api.db.nomics.world, v22 JSON).

DBnomics aggregates official statistical providers (ECB, IMF, OECD, Eurostat,
...) behind one REST API. Series are addressed as {provider}/{dataset}/{code}
with dot-separated codes, e.g. ECB EXR daily CNY/EUR spot = `D.CNY.EUR.SP00.A`.

Provider whitelist (brief: W1-C/W2-8): ECB / IMF / OECD mandatory, Eurostat
(ESTAT) optional. Sequences naming any other provider are refused BEFORE any
HTTP request — the API hosts hundreds of providers (incl. SAFE, see below) and
we only ever call measured-valid ones.

SAFE (国家外管局) freeze warning: DBnomics mirrors SAFE series but that mirror
is FROZEN at 2020-12 (only 12 observations). SAFE must never be wired as an
ongoing source — see README.md.

Entry point (fd-runner): run_dbnomics_api(limit=100, default_sequences=None)
-> list[dict]

Network: overseas source — direct first; only if a direct connection fails at
the connection layer, probe 127.0.0.1:7890 and retry once through that proxy.
The working mode is remembered (sticky). HTTP 5xx/empty responses are logged
and the sequence skipped — no retries, no anti-bot bypass attempts. Measured
2026-10-02: direct works (HTTP 200), no proxy needed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
from datetime import datetime, timezone

from scrapling.fetchers import FetcherSession

logger = logging.getLogger("dbnomics-api")
logger.addHandler(logging.NullHandler())

BASE_URL = "https://api.db.nomics.world/v22/series"
FALLBACK_PROXY = "http://127.0.0.1:7890"
HTTP_TIMEOUT = 25

HDRS = {
    "Accept": "application/json",
    "User-Agent": "fd-industry-data/dbnomics-api (batch data pipeline)",
}

# Brief-mandated provider whitelist: IMF/OECD/ECB, Eurostat optional.
# Anything else (incl. SAFE, whose DBnomics mirror froze at 2020-12) is refused.
PROVIDER_WHITELIST = frozenset({"ECB", "IMF", "OECD", "ESTAT"})

# Default sequence: ECB daily CNY/EUR reference (spot, average) rate.
DEFAULT_SEQUENCES: list[dict] = [
    {"provider": "ECB", "dataset": "EXR", "code": "D.CNY.EUR.SP00.A"},
]

# sticky network mode resolved at runtime: None | "direct" | "proxy"
NET_MODE: str | None = None


def _normalize_sequence(seq) -> dict:
    """Accept {"provider","dataset","code"} dicts or 'P/D/C' strings; refuse
    non-whitelisted providers before any request is made."""
    if isinstance(seq, str):
        parts = seq.strip("/").split("/")
        if len(parts) != 3 or not all(parts):
            raise ValueError(
                f"dbnomics sequence {seq!r} must be 'provider/dataset/code'"
            )
        seq = dict(zip(("provider", "dataset", "code"), parts))
    if not isinstance(seq, dict):
        raise ValueError(f"dbnomics sequence {seq!r} must be a dict or 'P/D/C' string")
    provider = str(seq.get("provider", "")).strip().upper()
    dataset = str(seq.get("dataset", "")).strip()
    code = str(seq.get("code", "")).strip()
    if not (provider and dataset and code):
        raise ValueError(f"dbnomics sequence {seq!r} needs provider, dataset and code")
    if provider not in PROVIDER_WHITELIST:
        raise ValueError(
            f"provider {provider!r} outside whitelist {sorted(PROVIDER_WHITELIST)}; "
            "request refused (SAFE mirror is frozen at 2020-12 — never wire it)"
        )
    return {"provider": provider, "dataset": dataset, "code": code}


def _proxy_port_open(host: str = "127.0.0.1", port: int = 7890, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def _get_json(url: str, proxy: str | None) -> tuple[int | None, bytes]:
    async with FetcherSession(
        impersonate="chrome120", timeout=HTTP_TIMEOUT, verify=False, proxy=proxy
    ) as session:
        resp = await session.get(url, headers=HDRS)
        return resp.status, resp.body or b""


def _load_doc(provider: str, url: str, body: bytes) -> list[dict]:
    """Extract the series docs list from a v22 payload; None-shaped failures
    return [] and the sequence is skipped without writing dirty rows."""
    try:
        doc = json.loads(body)
    except json.JSONDecodeError as exc:
        logger.warning("[%s] non-JSON response from %s: %s", provider, url, exc)
        return []
    if not isinstance(doc, dict):
        logger.warning("[%s] unexpected payload type %s, skipped", provider, type(doc).__name__)
        return []
    errors = doc.get("errors")
    if errors:
        logger.warning("[%s] API reported errors %s, skipped", provider, errors)
        return []
    series = doc.get("series") or {}
    docs = series.get("docs")
    if not isinstance(docs, list) or not docs:
        logger.warning("[%s] no series docs in payload from %s, skipped", provider, url)
        return []
    return docs


async def _fetch_docs(url: str, provider: str) -> list[dict]:
    """One attempt per network mode. 5xx/empty → log-and-skip, no retry storm.

    Proxy fallback only triggers on a connection-level failure of direct;
    an HTTP error status means the server was reached, so we never retry it.
    """
    global NET_MODE
    if NET_MODE in (None, "direct"):
        try:
            status, body = await _get_json(url, proxy=None)
        except Exception as exc:  # connection layer only
            logger.warning("[%s] direct connection error: %s", provider, exc)
            status, body = None, b""
        if status == 200 and body:
            NET_MODE = "direct"
            return _load_doc(provider, url, body)
        if status is not None:
            logger.warning(
                "[%s] direct HTTP %s (%d bytes) → log-and-skip, no retry",
                provider, status, len(body),
            )
            NET_MODE = "direct"
            return []
    if NET_MODE in (None, "proxy"):
        if not _proxy_port_open():
            logger.error("[%s] direct failed and fallback proxy 127.0.0.1:7890 is not open", provider)
            return []
        try:
            status, body = await _get_json(url, proxy=FALLBACK_PROXY)
        except Exception as exc:
            logger.warning("[%s] proxy connection error: %s", provider, exc)
            return []
        if status == 200 and body:
            NET_MODE = "proxy"
            return _load_doc(provider, url, body)
        logger.warning(
            "[%s] proxy HTTP %s (%d bytes) → log-and-skip, no retry", provider, status, len(body)
        )
    return []


def _doc_rows(doc: dict, url: str, scraped_at: str) -> list[dict]:
    """Flatten one series doc into observation rows.

    DBnomics v22 inlines observations as parallel arrays: doc['period'] is a
    list of YYYY-MM-DD period-start days, doc['value'] the aligned values.
    Missing observations are the string 'NA' — skipped, never fabricated.
    """
    provider = str(doc.get("provider_code", ""))
    dataset = str(doc.get("dataset_code", ""))
    series_code = str(doc.get("series_code", ""))
    series_name = str(doc.get("series_name", ""))
    periods = doc.get("period") or []
    values = doc.get("value") or []
    if not isinstance(periods, list) or not isinstance(values, list):
        logger.warning("[%s/%s] period/value are not arrays, skipped", provider, series_code)
        return []
    if len(periods) != len(values):
        logger.warning(
            "[%s/%s] %d periods vs %d values, skipped", provider, series_code, len(periods), len(values)
        )
        return []
    rows: list[dict] = []
    for period, val in zip(periods, values):
        if val is None or isinstance(val, bool) or not isinstance(val, (int, float, str)):
            continue
        if isinstance(val, str):  # 'NA' or other missing-value markers
            val = val.strip()
            try:
                val = float(val)
            except ValueError:
                continue
        rows.append({
            "provider": provider,
            "dataset": dataset,
            "series_code": series_code,
            "series_name": series_name,
            "period": str(period),
            "value": float(val),
            "scraped_at": scraped_at,
            "source_url": url,
        })
    return rows


async def _run(limit: int, sequences: list[dict]) -> list[dict]:
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    for seq in sequences:
        if len(rows) >= limit:
            break
        url = f"{BASE_URL}/{seq['provider']}/{seq['dataset']}/{seq['code']}?observations=1"
        docs = await _fetch_docs(url, seq["provider"])
        got: list[dict] = []
        for doc in docs:
            got.extend(_doc_rows(doc, url, scraped_at))
        logger.info(
            "[%s/%s/%s] parsed %d observations from %s",
            seq["provider"], seq["dataset"], seq["code"], len(got), url,
        )
        # keep the most recent `limit` observations of this series, chronological
        rows.extend(got[-limit:] if len(got) > limit else got)
    return rows[:limit]


def run_dbnomics_api(limit: int = 100, default_sequences: list | None = None) -> list[dict]:
    """fd-runner entry point. Returns at most `limit` rows.

    default_sequences: optional list of sequences, each a
    'provider/dataset/code' string or a dict with provider/dataset/code keys.
    Providers outside {ECB, IMF, OECD, ESTAT} are refused (ValueError) before
    any HTTP request. Default: ECB EXR D.CNY.EUR.SP00.A.
    """
    seqs = [_normalize_sequence(s) for s in (default_sequences or DEFAULT_SEQUENCES)]
    return asyncio.run(_run(max(0, int(limit)), seqs))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    for row in run_dbnomics_api(limit=5):
        print(json.dumps(row, ensure_ascii=False))
