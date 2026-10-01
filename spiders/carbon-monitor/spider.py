"""Carbon Monitor daily CO2 spider (datas.carbonmonitor.org full-dataset CSV).

Datasets (fixed whitelist; every claim below was probed live on 2026-10-02):
- carbon_global  Global daily CO2 emissions, country x sector, 2019-01-01 onward,
                 ~650k rows / ~34 MB, published with ~2 months lag.
- energy_global  Global daily energy consumption by fuel (sectors = Coal, Gas,
                 Oil, Nuclear, Hydroelectricity, Solar, Wind, Other sources),
                 near-zero lag. NOTE: the endpoint has NO electricity-specific
                 source value — carbon_electricity, electricity, world_electricity,
                 china_electricity, electricity_global, global_electricity,
                 elec_global, carbon_power, power_global all return a PHP fatal
                 error page; energy_global is the per-fuel companion dataset the
                 official site itself ships (carbonmonitor.org/downloadData.php).

Endpoint hard facts (measured):
- downloadFullDataset.php streams the ENTIRE dataset (no paging, no date
  filter); the server is slow (34 MB class) -> HTTP timeout >=300s required.
- Header line is `country,date,sector,value,` (trailing empty 5th column).
- Dates are DD/MM/YYYY -> normalized to YYYY-MM-DD.
- Invalid source= values return HTTP 200 with a PHP error page, so the payload
  is validated as CSV (header sniff) before parsing.
- The legacy `db.` domain is dead and must never be used.

Entry point (fd-runner): run_carbon_monitor(limit=100) -> list[dict], newest
rows first, at most `limit` rows. The second dataset is exposed via the
`source` parameter of the same entry function (source="energy_global").
`download_full_csv()` is the internal helper that downloads the full CSV to a
temp file and reports size/row count without building row dicts.

Network: overseas source — direct first; only on a connection-level direct
failure, probe 127.0.0.1:7890 and try once through that proxy (sticky mode).
Compressed transfer (Accept-Encoding: gzip) is always requested; curl_cffi
decodes, plus a gzip-magic guard in case a body arrives still encoded.
Failure discipline: one attempt per network mode, log-and-skip, NEVER
consecutive retries. Daily cadence = at most one full pull per day (frequency
"daily" in the manifest + in-process cache reuse for calls within one job).
"""
from __future__ import annotations

import asyncio
import csv
import gzip
import logging
import os
import socket
import tempfile
import time
from datetime import datetime, timezone

from scrapling.fetchers import FetcherSession

logger = logging.getLogger("carbon-monitor")
logger.addHandler(logging.NullHandler())

BASE_URL = "https://datas.carbonmonitor.org/API/downloadFullDataset.php"
FALLBACK_PROXY = "http://127.0.0.1:7890"
# Brief hard rule: >=300s. Measured 2026-10-02: a full ~34 MB pull takes ~2.5-4 min
# when the server is fast, but it also throttles (7.6 MB per 300 s observed), so
# the window is set to 540 s to give one attempt a fair chance.
HTTP_TIMEOUT = 540

HDRS = {
    "Accept": "text/csv,application/csv,text/plain,*/*",
    "Accept-Encoding": "gzip",  # --compressed equivalent; curl_cffi auto-decodes
    "User-Agent": "fd-industry-data/carbon-monitor (batch data pipeline)",
}

# Measured-valid source= values ONLY; anything else is never sent upstream
# (invalid values make the endpoint throw a PHP error page).
VALID_SOURCES: dict[str, str] = {
    "carbon_global": "daily CO2 emissions by country and sector (Mt CO2/day per upstream docs)",
    "energy_global": "daily energy consumption by fuel sector (unit not declared in the CSV)",
}
DEFAULT_SOURCE = "carbon_global"
CACHE_MAX_AGE_S = 6 * 3600  # in-process reuse window: at most one pull per job

NET_MODE: str | None = None  # sticky resolved network mode: None | "direct" | "proxy"
_FULL_CACHE: dict[str, dict] = {}  # source -> download_full_csv() result (same process)
_DATE_MEMO: dict[str, str | None] = {}  # raw DD/MM/YYYY -> YYYY-MM-DD


def _proxy_port_open(host: str = "127.0.0.1", port: int = 7890, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _is_csv_payload(body: bytes) -> bool:
    """Header sniff — invalid source= yields HTTP 200 + PHP error page."""
    return body[:64].lstrip(b"\xef\xbb\xbf").startswith(b"country,date,sector,value")


async def _get_csv_bytes(url: str, proxy: str | None) -> tuple[int | None, bytes]:
    async with FetcherSession(
        impersonate="chrome120", timeout=HTTP_TIMEOUT, verify=False, proxy=proxy,
        retries=1,  # scrapling semantics: TOTAL attempts (range(max_retries)) — 1
        retry_delay=0,  # = exactly one shot; retries=0 is broken upstream (session
        # never opens: "No active session available"), so 1 is the discipline floor.
    ) as session:
        resp = await session.get(url, headers=HDRS)
        body = resp.body or b""
        if body[:2] == b"\x1f\x8b":  # gzip magic — decode if curl_cffi did not
            body = gzip.decompress(body)
        return resp.status, body


async def _download_bytes(source: str) -> tuple[bytes | None, str]:
    """One attempt per network mode. 5xx/error-page -> log-and-skip, no retry.

    Proxy fallback triggers only on a connection-level failure of direct; a bad
    HTTP status or non-CSV payload means the server was reached, so it is never
    retried against the proxy either.
    """
    global NET_MODE
    url = f"{BASE_URL}?source={source}"
    if NET_MODE in (None, "direct"):
        try:
            status, body = await _get_csv_bytes(url, proxy=None)
        except Exception as exc:  # connection layer only (incl. timeout)
            logger.warning("[carbon-monitor] direct connection error: %s", exc)
            status, body = None, b""
        if status == 200 and _is_csv_payload(body):
            NET_MODE = "direct"
            return body, "direct"
        if status is not None:
            logger.warning(
                "[carbon-monitor] direct HTTP %s (%d bytes) -> log-and-skip, no retry",
                status, len(body),
            )
            NET_MODE = "direct"
            return None, "direct"
    if NET_MODE in (None, "proxy"):
        if not _proxy_port_open():
            logger.error("[carbon-monitor] direct failed and fallback proxy 127.0.0.1:7890 is not open")
            return None, "unreachable"
        try:
            status, body = await _get_csv_bytes(url, proxy=FALLBACK_PROXY)
        except Exception as exc:
            logger.warning("[carbon-monitor] proxy connection error: %s", exc)
            return None, "unreachable"
        if status == 200 and _is_csv_payload(body):
            NET_MODE = "proxy"
            return body, "proxy"
        logger.warning(
            "[carbon-monitor] proxy HTTP %s (%d bytes) -> log-and-skip, no retry",
            status, len(body),
        )
    return None, "unreachable"


def download_full_csv(source: str = DEFAULT_SOURCE, dest_path: str | None = None) -> dict:
    """Full-dataset download (internal acceptance helper).

    Streams the complete CSV for `source` into a file WITHOUT building row
    dicts, so the dataset-size acceptance (>=600k rows) is verifiable cheaply.
    The caller owns the returned file's lifetime (it is NOT auto-deleted while
    the in-process cache may still reference it; it lives in the system temp
    dir, nothing is written inside the repo).
    """
    if source not in VALID_SOURCES:
        raise ValueError(f"unknown source {source!r}; valid: {sorted(VALID_SOURCES)}")
    t0 = time.monotonic()
    body, net = asyncio.run(_download_bytes(source))
    elapsed = time.monotonic() - t0
    if body is None:
        return {"ok": False, "source": source, "net_mode": net, "elapsed_s": round(elapsed, 1)}
    if dest_path is None:
        fd, dest_path = tempfile.mkstemp(prefix=f"carbon-monitor-{source}-", suffix=".csv")
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
    else:
        with open(dest_path, "wb") as fh:
            fh.write(body)
    n_newlines = body.count(b"\n")
    total_lines = n_newlines if body.endswith(b"\n") else n_newlines + 1
    return {
        "ok": True,
        "source": source,
        "path": dest_path,
        "bytes": len(body),
        "data_rows": max(0, total_lines - 1),  # minus header line
        "header": body.split(b"\n", 1)[0].decode("utf-8", "replace").strip(),
        "net_mode": net,
        "elapsed_s": round(elapsed, 1),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _full_csv_cached(source: str) -> dict:
    """download_full_csv with a small in-process reuse window (daily discipline:
    repeated calls inside one job reuse the same full pull)."""
    hit = _FULL_CACHE.get(source)
    if hit and hit.get("ok") and os.path.exists(hit.get("path", "")):
        age = time.monotonic() - hit.get("_mono", 0)
        if age < CACHE_MAX_AGE_S:
            logger.info("[carbon-monitor] reusing full CSV fetched %.0fs ago", age)
            return hit
    res = download_full_csv(source)
    if res.get("ok"):
        res["_mono"] = time.monotonic()
        _FULL_CACHE[source] = res
    return res


def _iso_date(raw: str) -> str | None:
    """DD/MM/YYYY (measured upstream format) -> YYYY-MM-DD; None if unparseable."""
    raw = raw.strip()
    if raw in _DATE_MEMO:
        return _DATE_MEMO[raw]
    iso: str | None = None
    try:
        iso = datetime.strptime(raw, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        pass  # leave blank rather than guess a wrong day/month order
    _DATE_MEMO[raw] = iso
    return iso


def _sort_key(line: bytes) -> str | None:
    """Cheap extraction of the date sort key (lexicographic == chronological)."""
    parts = line.split(b'","')
    if len(parts) < 2:
        return None
    return _iso_date(parts[1].decode("utf-8", "replace"))


def _parse_line(line: str, source: str, url: str, scraped_at: str) -> dict | None:
    """Parse one CSV line into a row dict; parse failure -> None (no dirty rows)."""
    try:
        parts = next(csv.reader([line]))
    except (csv.Error, StopIteration):
        return None
    if len(parts) < 4:
        return None
    country, raw_date, sector, raw_value = (p.strip() for p in parts[:4])
    iso = _iso_date(raw_date)
    if not country or iso is None:
        return None
    try:
        value = float(raw_value)
    except ValueError:
        return None
    return {
        "date": iso,
        "country": country,
        "sector": sector,
        "value": value,
        "dataset": source,
        "scraped_at": scraped_at,
        "source_url": url,
    }


def _newest_rows(path: str, limit: int, source: str) -> list[dict]:
    """Memory-lean newest-first selection: index (date, offset) pairs, sort,
    then parse only the top-`limit` lines instead of ~650k dicts."""
    if limit <= 0:
        return []
    index: list[tuple[str, int]] = []
    with open(path, "rb") as fh:
        fh.readline()  # header
        offset = fh.tell()
        for line in fh:
            key = _sort_key(line)
            if key:
                index.append((key, offset))
            offset = fh.tell()
    index.sort(key=lambda kv: kv[0], reverse=True)
    url = f"{BASE_URL}?source={source}"
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    with open(path, "rb") as fh:
        for _, off in index[:limit]:
            fh.seek(off)
            line = fh.readline().decode("utf-8", "replace").strip().lstrip("﻿")
            row = _parse_line(line, source, url, scraped_at)
            if row:
                rows.append(row)
    return rows


def run_carbon_monitor(limit: int = 100, source: str = DEFAULT_SOURCE) -> list[dict]:
    """fd-runner entry point. Full-pull -> newest-first, at most `limit` rows.

    source="energy_global" selects the per-fuel companion dataset (see the
    module docstring for why no electricity-named source exists upstream).
    """
    limit = max(0, int(limit))
    if source not in VALID_SOURCES:
        logger.warning("[carbon-monitor] unknown source %r -> falling back to %s", source, DEFAULT_SOURCE)
        source = DEFAULT_SOURCE
    res = _full_csv_cached(source)
    if not res.get("ok"):
        return []
    return _newest_rows(res["path"], limit, source)


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    for row in run_carbon_monitor(limit=5):
        print(json.dumps(row, ensure_ascii=False))
