"""Eurostat dissemination API spider (JSON-stat 2.0).

Datasets (fixed whitelist; all combos measured live on 2026-10-02):
- sts_inpr_m   Production in industry, monthly — manufacturing production index
               (nace_r2=C manufacturing, C20 chemicals; unit=I21, s_adj=NSA, indic_bt=PRD)
- ei_bsco_m    Consumer confidence, monthly — composite indicator BS-CSMCI, unit=BAL
               (NOTE: ei_bsci is dead upstream, HTTP 404 — never use it)
- apri_pi_outq Agricultural output price index, quarterly
               (am_item=AM010000 cereals incl. seeds, p_adj=NI nominal, unit=I20)
               (NOTE: apri_pi05_outq is dead upstream — never use it)

JSON-stat 2.0 hard constraint: the response `value` object is {flat_index: number}
and null cells are OMITTED. Coordinates must always be rebuilt from
`id`/`size`/`dimension[].category.index` (row-major strides); never assume the
value dict order matches anything.

Entry point (fd-runner): run_eurostat_api(limit=100) -> list[dict]

Network: overseas source — direct first; only if a direct connection fails at the
connection layer, probe 127.0.0.1:7890 and retry once through that proxy. The
working mode is remembered (sticky). HTTP 5xx/empty responses are logged and the
dataset skipped — no retries, no anti-bot bypass attempts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
from datetime import datetime, timezone
from urllib.parse import urlencode

from scrapling.fetchers import FetcherSession

logger = logging.getLogger("eurostat-api")
logger.addHandler(logging.NullHandler())

BASE_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
FALLBACK_PROXY = "http://127.0.0.1:7890"
HTTP_TIMEOUT = 25

HDRS = {
    "Accept": "application/json",
    "User-Agent": "fd-industry-data/eurostat-api (batch data pipeline)",
}

GEOS = ["DE", "FR", "IT"]

# dataset x dimension whitelist — only measured-valid parameters are ever sent.
# sts_inpr_m pins since=2015-01 (acceptance window 2015-01→latest, ≥130 obs/country);
# ei_bsco_m / apri_pi_outq backfill a rolling 12 months at crawl time.
DATASETS: list[dict] = [
    {
        "dataset": "sts_inpr_m",
        "dims": {
            "freq": "M",
            "indic_bt": "PRD",
            "nace_r2": ["C", "C20"],
            "s_adj": "NSA",
            "unit": "I21",
        },
        "geos": GEOS,
        "since": "2015-01",
        "colmap": {"indic": "indic_bt", "nace_r2": "nace_r2", "s_adj": "s_adj", "p_adj": None},
    },
    {
        "dataset": "ei_bsco_m",
        "dims": {"freq": "M", "indic": "BS-CSMCI", "s_adj": "SA", "unit": "BAL"},
        "geos": GEOS,
        "since": None,  # rolling 12-month backfill, month grain
        "colmap": {"indic": "indic", "nace_r2": None, "s_adj": "s_adj", "p_adj": None},
    },
    {
        "dataset": "apri_pi_outq",
        "dims": {"freq": "Q", "am_item": "AM010000", "p_adj": "NI", "unit": "I20"},
        "geos": GEOS,
        "since": None,  # rolling 12-month backfill, quarter grain
        "colmap": {"indic": "am_item", "nace_r2": None, "s_adj": None, "p_adj": "p_adj"},
    },
]

# sticky network mode resolved at runtime: None | "direct" | "proxy"
NET_MODE: str | None = None


def _since_months(months: int = 12) -> str:
    """UTC date `months` back as YYYY-MM (Eurostat time format)."""
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month - months
    while month <= 0:
        month += 12
        year -= 1
    return f"{year:04d}-{month:02d}"


def _since_quarters(months: int = 12) -> str:
    """UTC date `months` back snapped to its quarter as YYYY-QN."""
    year, month = _since_months(months).split("-")
    quarter = (int(month) - 1) // 3 + 1
    return f"{year}-Q{quarter}"


def _build_url(cfg: dict) -> str:
    params: list[tuple[str, str]] = [("format", "JSON"), ("lang", "EN")]
    for dim, val in cfg["dims"].items():
        for v in (val if isinstance(val, list) else [val]):
            params.append((dim, v))
    for geo in cfg["geos"]:
        params.append(("geo", geo))
    since = cfg["since"] or (
        _since_quarters() if cfg["dims"].get("freq") == "Q" else _since_months()
    )
    params.append(("sinceTimePeriod", since))
    return f"{BASE_URL}/{cfg['dataset']}?{urlencode(params)}"


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


def _load_doc(dataset: str, url: str, body: bytes) -> dict | None:
    try:
        doc = json.loads(body)
    except json.JSONDecodeError as exc:
        logger.warning("[%s] non-JSON response from %s: %s", dataset, url, exc)
        return None
    if not isinstance(doc, dict) or doc.get("class") != "dataset":
        logger.warning("[%s] unexpected payload class=%r, skipped", dataset, doc.get("class") if isinstance(doc, dict) else type(doc))
        return None
    return doc


async def _fetch_doc(url: str, dataset: str) -> dict | None:
    """One attempt per network mode. 5xx/empty → log-and-skip, no retry storm.

    Proxy fallback only triggers on a connection-level failure of direct;
    an HTTP error status means the server was reached, so we never retry it.
    """
    global NET_MODE
    if NET_MODE in (None, "direct"):
        try:
            status, body = await _get_json(url, proxy=None)
        except Exception as exc:  # connection layer only
            logger.warning("[%s] direct connection error: %s", dataset, exc)
            status, body = None, b""
        if status == 200 and body:
            NET_MODE = "direct"
            return _load_doc(dataset, url, body)
        if status is not None:
            logger.warning(
                "[%s] direct HTTP %s (%d bytes) → log-and-skip, no retry", dataset, status, len(body)
            )
            NET_MODE = "direct"
            return None
    if NET_MODE in (None, "proxy"):
        if not _proxy_port_open():
            logger.error("[%s] direct failed and fallback proxy 127.0.0.1:7890 is not open", dataset)
            return None
        try:
            status, body = await _get_json(url, proxy=FALLBACK_PROXY)
        except Exception as exc:
            logger.warning("[%s] proxy connection error: %s", dataset, exc)
            return None
        if status == 200 and body:
            NET_MODE = "proxy"
            return _load_doc(dataset, url, body)
        logger.warning(
            "[%s] proxy HTTP %s (%d bytes) → log-and-skip, no retry", dataset, status, len(body)
        )
    return None


def _jsonstat_rows(doc: dict, colmap: dict, dataset: str, source_url: str, scraped_at: str) -> list[dict]:
    """Flatten a JSON-stat 2.0 dataset into row dicts.

    Null cells are omitted from `value`, so coordinates are rebuilt from the
    declared grid (id/size + per-dimension category index) via row-major
    strides. A flat index outside the grid is a parse failure → skipped.
    """
    ids: list[str] = doc["id"]
    sizes: list[int] = doc["size"]
    dimensions = doc.get("dimension", {}) or {}

    codes: dict[str, list[str]] = {}
    for dim, size in zip(ids, sizes):
        cat = (dimensions.get(dim) or {}).get("category") or {}
        index = cat.get("index")
        if isinstance(index, dict):
            ordered = [code for code, _ in sorted(index.items(), key=lambda kv: kv[1])]
        elif isinstance(index, list):
            ordered = [str(code) for code in index]
        else:  # category index absent → implicit 0..size-1
            ordered = [str(i) for i in range(size)]
        if len(ordered) != size:
            logger.warning(
                "[%s] dimension %s declares %d codes but header size is %d",
                dataset, dim, len(ordered), size,
            )
        codes[dim] = ordered

    # row-major strides: first id varies slowest, last (usually `time`) fastest
    strides = [1] * len(ids)
    for i in range(len(ids) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]

    value = doc.get("value") or {}
    if isinstance(value, list):  # JSON-stat also allows array form
        value = {i: v for i, v in enumerate(value) if v is not None}

    rows: list[dict] = []
    for flat, val in value.items():
        try:
            pos = int(flat)
        except (TypeError, ValueError):
            logger.warning("[%s] non-integer flat index %r, skipped", dataset, flat)
            continue
        if val is None or isinstance(val, bool) or not isinstance(val, (int, float)):
            continue  # null / non-numeric cell → skip, never fabricate
        coords: dict[str, int] = {}
        rem = pos
        for dim, stride in zip(ids, strides):
            coords[dim], rem = divmod(rem, stride)
        try:
            dim_values = {dim: codes[dim][coords[dim]] for dim in ids}
        except (IndexError, KeyError):
            logger.warning("[%s] flat index %s outside declared grid, skipped", dataset, flat)
            continue
        row = {
            "dataset": dataset,
            "geo": dim_values.get("geo", ""),
            "freq": dim_values.get("freq", ""),
            "unit": dim_values.get("unit", ""),
            "period": dim_values.get("time", ""),
            "value": float(val),
            "scraped_at": scraped_at,
            "source_url": source_url,
        }
        for col, dim in colmap.items():
            row[col] = dim_values.get(dim, "") if dim else ""
        rows.append(row)
    return rows


async def _run(limit: int) -> list[dict]:
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    for cfg in DATASETS:
        if len(rows) >= limit:
            break
        url = _build_url(cfg)
        doc = await _fetch_doc(url, cfg["dataset"])
        if doc is None:
            continue
        got = _jsonstat_rows(doc, cfg["colmap"], cfg["dataset"], url, scraped_at)
        logger.info("[%s] parsed %d rows from %s", cfg["dataset"], len(got), url)
        rows.extend(got)
    return rows[:limit]


def run_eurostat_api(limit: int = 100) -> list[dict]:
    """fd-runner entry point. Returns at most `limit` rows."""
    return asyncio.run(_run(max(0, int(limit))))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    for row in run_eurostat_api(limit=5):
        print(json.dumps(row, ensure_ascii=False))
