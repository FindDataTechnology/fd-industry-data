"""IMF PortWatch daily ports/chokepoints spider (ArcGIS FeatureServer).

Two table services on the PortWatch ArcGIS Online organization
(services9.arcgis.com/weJ1QsnbMYJlCHdG), both plain tables (no geometry):
- Daily_Ports_Data      one row per port per day  (portcalls + import/export tonnes)
- Daily_Chokepoints_Data one row per chokepoint per day (transits + transiting tonnes)

Measured live on 2026-10-02: ports publish 2065 rows/day (latest day 2026-09-25,
lag 7 days), chokepoints 28 rows/day (latest day 2026-09-27, lag 5 days); both
services run 2019-01-01..latest with every day fully populated. NOTE: older
PortWatch materials advertise ~6880 ports / 42 chokepoints — the current tables
hold 2065 ports and 28 chokepoints per day (PortWatch rebuilt the databases).

Pagination hard rules (W2-10 field-tested):
- day filter `where=date>date'D-1'` (esriFieldTypeDateOnly; strict > on a
  date-only literal selects exactly day D);
- `resultOffset` + `resultRecordCount=1000` paging, continue ONLY while
  `exceededTransferLimit=true`, stop on an empty page;
- `orderByFields=portid` — ordering by the date field times out server-side;
- `f=json`, `outFields=*`. No retry storms: one attempt per network mode.

The latest available day is resolved per service with an outStatistics MAX(date)
query (fast, measured ~1s); the spider defaults to that day (data lag ~7 days).

Entry point (fd-runner): run_portwatch_imf(limit=100) -> list[dict]

Network: overseas source — direct first; only if a direct connection fails at
the connection layer, probe 127.0.0.1:7890 and retry once through that proxy.
The working mode is remembered (sticky). HTTP 5xx/empty responses are logged and
the service skipped — no retries, no anti-bot bypass attempts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from scrapling.fetchers import FetcherSession

logger = logging.getLogger("portwatch-imf")
logger.addHandler(logging.NullHandler())

ORG_BASE = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
SERVICES: dict[str, str] = {
    "ports": f"{ORG_BASE}/Daily_Ports_Data/FeatureServer/0/query",
    "chokepoints": f"{ORG_BASE}/Daily_Chokepoints_Data/FeatureServer/0/query",
}
FALLBACK_PROXY = "http://127.0.0.1:7890"
HTTP_TIMEOUT = 25
PAGE_SIZE = 1000  # server maxRecordCount; larger values are rejected
MAX_PAGES = 200  # safety bound: a full day is ~2-3 pages, 200 pages = 200k rows

HDRS = {
    "Accept": "application/json",
    "User-Agent": "fd-industry-data/portwatch-imf (batch data pipeline)",
}

# sticky network mode resolved at runtime: None | "direct" | "proxy"
NET_MODE: str | None = None


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


def _load_doc(url: str, body: bytes) -> dict | None:
    try:
        doc = json.loads(body)
    except json.JSONDecodeError as exc:
        logger.warning("non-JSON response from %s: %s", url, exc)
        return None
    if not isinstance(doc, dict) or doc.get("error"):
        logger.warning("ArcGIS error payload from %s: %s", url, (doc or {}).get("error"))
        return None
    return doc


async def _fetch_doc(url: str, tag: str) -> dict | None:
    """One attempt per network mode. 5xx/empty → log-and-skip, no retry storm.

    Proxy fallback only triggers on a connection-level failure of direct;
    an HTTP error status means the server was reached, so we never retry it.
    """
    global NET_MODE
    if NET_MODE in (None, "direct"):
        try:
            status, body = await _get_json(url, proxy=None)
        except Exception as exc:  # connection layer only
            logger.warning("[%s] direct connection error: %s", tag, exc)
            status, body = None, b""
        if status == 200 and body:
            NET_MODE = "direct"
            return _load_doc(url, body)
        if status is not None:
            logger.warning(
                "[%s] direct HTTP %s (%d bytes) → log-and-skip, no retry", tag, status, len(body)
            )
            NET_MODE = "direct"
            return None
    if NET_MODE in (None, "proxy"):
        if not _proxy_port_open():
            logger.error("[%s] direct failed and fallback proxy 127.0.0.1:7890 is not open", tag)
            return None
        try:
            status, body = await _get_json(url, proxy=FALLBACK_PROXY)
        except Exception as exc:
            logger.warning("[%s] proxy connection error: %s", tag, exc)
            return None
        if status == 200 and body:
            NET_MODE = "proxy"
            return _load_doc(url, body)
        logger.warning(
            "[%s] proxy HTTP %s (%d bytes) → log-and-skip, no retry", tag, status, len(body)
        )
    return None


async def _latest_day(endpoint: str, service: str) -> date | None:
    """MAX(date) via outStatistics (server-side; ordering by date would time out)."""
    stats = [{"statisticType": "max", "onStatisticField": "date", "outStatisticFieldName": "maxdate"}]
    url = f"{endpoint}?{urlencode({'f': 'json', 'where': '1=1', 'outStatistics': json.dumps(stats), 'returnGeometry': 'false'})}"
    doc = await _fetch_doc(url, f"{service}:maxdate")
    if doc is None:
        return None
    raw = ((doc.get("features") or [{}])[0].get("attributes") or {}).get("maxdate")
    if not raw:
        logger.warning("[%s] MAX(date) came back empty — service has no rows?", service)
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        logger.warning("[%s] unparsable MAX(date) %r", service, raw)
        return None


def _page_url(endpoint: str, where: str, offset: int) -> str:
    params = {
        "f": "json",
        "where": where,
        "outFields": "*",
        "returnGeometry": "false",
        "orderByFields": "portid",  # ordering by date times out server-side
        "resultOffset": str(offset),
        "resultRecordCount": str(PAGE_SIZE),
    }
    return f"{endpoint}?{urlencode(params)}"


def _num(attrs: dict[str, Any], key: str) -> int | None:
    """Upstream integer as-is; None stays None (never fabricated to 0)."""
    val = attrs.get(key)
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return int(val)


def _port_row(attrs: dict[str, Any], scraped_at: str, source_url: str) -> dict | None:
    port_id = attrs.get("portid")
    day = attrs.get("date")
    if not port_id or not day:
        return None  # missing join keys → dirty row, skip
    return {
        "service": "ports",
        "port_id": str(port_id),
        "port_name": str(attrs.get("portname") or ""),
        "country": str(attrs.get("country") or ""),
        "iso3": str(attrs.get("ISO3") or ""),
        "date": str(day)[:10],
        "portcalls": _num(attrs, "portcalls"),
        "import_tonnes": _num(attrs, "import"),
        "export_tonnes": _num(attrs, "export"),
        "transit_vessels": None,
        "throughput_tonnes": None,
        "scraped_at": scraped_at,
        "source_url": source_url,
    }


def _chokepoint_row(attrs: dict[str, Any], scraped_at: str, source_url: str) -> dict | None:
    port_id = attrs.get("portid")
    day = attrs.get("date")
    if not port_id or not day:
        return None  # missing join keys → dirty row, skip
    return {
        "service": "chokepoints",
        "port_id": str(port_id),
        "port_name": str(attrs.get("portname") or ""),
        "country": "",
        "iso3": "",
        "date": str(day)[:10],
        "portcalls": None,
        "import_tonnes": None,
        "export_tonnes": None,
        "transit_vessels": _num(attrs, "n_total"),
        "throughput_tonnes": _num(attrs, "capacity"),
        "scraped_at": scraped_at,
        "source_url": source_url,
    }


_ROW_BUILDERS = {"ports": _port_row, "chokepoints": _chokepoint_row}


async def _fetch_latest_day(service: str, scraped_at: str, out: list[dict], limit: int) -> None:
    """Append day-D rows for one service to `out` (D = service's latest day).

    `date>date'D-1'` on an esriFieldTypeDateOnly field selects exactly day D
    (strict > against a date-only literal). Offset paging is safe here: the
    queried day is historical and immutable while we page.
    """
    endpoint = SERVICES[service]
    build_row = _ROW_BUILDERS[service]
    latest = await _latest_day(endpoint, service)
    if latest is None:
        return
    where = f"date>date'{(latest - timedelta(days=1)).isoformat()}'"
    logger.info("[%s] latest day %s, fetching with where=%s", service, latest.isoformat(), where)

    offset, pages, got = 0, 0, 0
    while len(out) < limit and pages < MAX_PAGES:
        url = _page_url(endpoint, where, offset)
        doc = await _fetch_doc(url, service)
        if doc is None:
            return  # upstream/HTTP failure → keep what earlier pages produced
        feats = doc.get("features") or []
        pages += 1
        for feat in feats:
            row = build_row(feat.get("attributes") or {}, scraped_at, url)
            if row is not None:
                out.append(row)
                got += 1
        # hard rules: continue only while exceededTransferLimit=true; empty page stops
        if not doc.get("exceededTransferLimit") or not feats:
            logger.info(
                "[%s] day %s complete: %d rows in %d page(s)",
                service, latest.isoformat(), got, pages,
            )
            return
        offset += PAGE_SIZE
    if pages >= MAX_PAGES:
        logger.warning("[%s] hit MAX_PAGES=%d safety bound, stopping pagination", service, MAX_PAGES)


async def _run(limit: int) -> list[dict]:
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    for service in ("ports", "chokepoints"):
        if len(rows) >= limit:
            break
        await _fetch_latest_day(service, scraped_at, rows, limit)
    return rows[:limit]


def run_portwatch_imf(limit: int = 100) -> list[dict]:
    """fd-runner entry point. Returns at most `limit` rows.

    limit caps TOTAL rows across both services; one full latest day is
    ~2065 ports rows + ~28 chokepoints rows, so pass limit >= ~2200 for a
    complete day (see README).
    """
    return asyncio.run(_run(max(0, int(limit))))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    for row in run_portwatch_imf(limit=5):
        print(json.dumps(row, ensure_ascii=False))
