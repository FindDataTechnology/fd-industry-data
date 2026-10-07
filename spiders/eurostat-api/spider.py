"""Eurostat dissemination API spider (JSON-stat 2.0 + comext SDMX-ML 2.1).

Datasets (fixed whitelist; all combos measured live on 2026-10-02, extensions
re-measured 2026-10-07):
- sts_inpr_m   Production in industry, monthly — manufacturing production index
               (nace_r2=C manufacturing, C20 chemicals; unit=I21, s_adj=NSA, indic_bt=PRD)
- ei_bsco_m    Consumer confidence, monthly — composite indicator BS-CSMCI, unit=BAL
               (NOTE: ei_bsci is dead upstream, HTTP 404 — never use it)
- apri_pi_outq Agricultural output price index, quarterly
               (am_item=AM010000 cereals incl. seeds, p_adj=NI nominal, unit=I20)
               (NOTE: apri_pi05_outq is dead upstream — never use it)

Batch2 Wave C extensions (one dedicated entry point each; same dissemination API):
- run_eurostat_c20     sts_inpr_m C20 chemicals, CA-adjusted, EU27_2020 aggregate
                       (indic_bt=PRD + unit=I21; s_adj=CA — NSA x C20 is empty)
- run_eurostat_bsci    ei_bsco_m consumer confidence, NSA variant, since 1980-01
- run_eurostat_apri    apri_pi_outq output price index, am_item=AM141000
- run_eurostat_comext  DS-045409 EU-China monthly trade, HS6 PV/LED family via the
                       api/comext/dissemination SDMX 2.1 segment (GenericData XML).
                       The /statistics/1.0 dissemination segment 404s for comext —
                       never route this dataset there. Reporter/partner use ISO
                       codes (EU27_2020/CN; legacy 1A/1Z are rejected with a silent
                       empty set). Products follow the HS revision break:
                       854140 data ends 2021-12, HS2022 codes 854141/854142/854143/
                       854149 carry 2022+. Flow direction re-measured 2026-10-07:
                       flow=1 = EU imports (the China→EU export view — the intended
                       series); flow=2 = EU exports. The initially merged flow=2
                       series was the wrong direction (EU→CN exports).

JSON-stat 2.0 hard constraint: the response `value` object is {flat_index: number}
and null cells are OMITTED. Coordinates must always be rebuilt from
`id`/`size`/`dimension[].category.index` (row-major strides); never assume the
value dict order matches anything.

Empty-set trap: Eurostat answers HTTP 200 with an empty `value` object (JSON-stat)
or a header-only GenericData envelope (comext) when a parameter combo has no data.
Every extension query therefore asserts a non-empty parse — an empty set is
treated as upstream/parameter drift, never as success.

Entry points (fd-runner):
- run_eurostat_api(limit=100) -> list[dict]        (original three datasets)
- run_eurostat_c20 / run_eurostat_bsci / run_eurostat_apri / run_eurostat_comext

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
import xml.etree.ElementTree as ET
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


# --------------------------------------------------------------------------
# Batch2 Wave C extensions — one dedicated entry point per dataset.
# All parameter combos below were re-measured live on 2026-10-07; frozen
# anchor values live in golden/002..005. Every query asserts a non-empty
# parse: Eurostat returns HTTP 200 with an empty set for unknown/renamed
# parameter combos, which must surface as a hard failure, not silence.
# --------------------------------------------------------------------------

C20_CFG: dict = {
    "dataset": "sts_inpr_m",
    # PROD is the dead code (empty set); PRD + CA is the only live C20/EU27 combo.
    "dims": {"freq": "M", "indic_bt": "PRD", "nace_r2": "C20", "s_adj": "CA", "unit": "I21"},
    "geos": ["EU27_2020"],
    "since": "2000-01",
    "colmap": {"indic": "indic_bt", "nace_r2": "nace_r2", "s_adj": "s_adj", "p_adj": None},
}

BSCI_CFG: dict = {
    "dataset": "ei_bsco_m",
    # NSA variant: series dimension starts 1980-01, first EU27_2020 value 1985-01
    # (null cells omitted upstream). SA is covered by the original run_eurostat_api.
    "dims": {"freq": "M", "indic": "BS-CSMCI", "s_adj": "NSA", "unit": "BAL"},
    "geos": ["EU27_2020"],
    "since": "1980-01",
    "colmap": {"indic": "indic", "nace_r2": None, "s_adj": "s_adj", "p_adj": None},
}

APRI_CFG: dict = {
    "dataset": "apri_pi_outq",
    # AM141000 (EU Agricultural Accounts industry 141000): values 2020-Q1..latest
    # under base 2020=100; the window since 2000-Q1 follows the measured brief.
    "dims": {"freq": "Q", "am_item": "AM141000", "p_adj": "NI", "unit": "I20"},
    "geos": ["EU27_2020"],
    "since": "2000-Q1",
    "colmap": {"indic": "am_item", "nace_r2": None, "s_adj": None, "p_adj": "p_adj"},
}


def _assert_nonempty(rows: list[dict], dataset: str, url: str) -> list[dict]:
    """Eurostat answers HTTP 200 with an empty set for dead parameter combos.

    Treat a parsed-empty result as drift and fail loud instead of logging an
    innocent-looking zero-row success.
    """
    if not rows:
        raise RuntimeError(
            f"[{dataset}] empty dataset returned (HTTP 200, 0 rows) for {url} — "
            "parameter drift upstream?"
        )
    return rows


async def _run_single(cfg: dict, limit: int) -> list[dict]:
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    url = _build_url(cfg)
    doc = await _fetch_doc(url, cfg["dataset"])
    if doc is None:
        raise RuntimeError(f"[{cfg['dataset']}] fetch failed (see warnings) for {url}")
    rows = _assert_nonempty(
        _jsonstat_rows(doc, cfg["colmap"], cfg["dataset"], url, scraped_at),
        cfg["dataset"], url,
    )
    logger.info("[%s] parsed %d rows from %s", cfg["dataset"], len(rows), url)
    return rows[:limit]


def run_eurostat_c20(limit: int = 100) -> list[dict]:
    """C20 chemicals production index (CA-adjusted, EU27_2020, monthly).

    Frozen anchor: 2026-07 = 81.8 (I21, PRD).
    """
    return asyncio.run(_run_single(C20_CFG, max(0, int(limit))))


def run_eurostat_bsci(limit: int = 100) -> list[dict]:
    """Consumer confidence indicator, NSA variant (EU27_2020, since 1980-01).

    Frozen anchor: 2026-09 = -15.6 (BAL, BS-CSMCI). ei_bsci is dead upstream (404).
    """
    return asyncio.run(_run_single(BSCI_CFG, max(0, int(limit))))


def run_eurostat_apri(limit: int = 100) -> list[dict]:
    """Agricultural output price index, AM141000 (EU27_2020, quarterly).

    Frozen anchor: 2026-Q2 = 135.29 (I20, NI). apri_pi05_outq is dead upstream (404).
    """
    return asyncio.run(_run_single(APRI_CFG, max(0, int(limit))))


# -- comext: EU-China trade via the SDMX 2.1 dissemination segment ------------

COMEXT_BASE_URL = "https://ec.europa.eu/eurostat/api/comext/dissemination/sdmx/2.1/data"
COMEXT_DATAFLOW = "DS-045409"  # EU trade since 1988 by HS6, monthly (DSD v6.5)
COMEXT_REPORTER = "EU27_2020"  # ISO codes only: legacy 1A/1Z -> silent empty set
COMEXT_PARTNER = "CN"
# Re-measured 2026-10-07 (double-probe verdict): 1 = EU imports, i.e. the
# China->EU export view — the intended series. flow=2 is EU exports; the
# initially merged flow=2 series collected the wrong direction. Evidence:
# HS 360410 (fireworks, CN-monopolized EU supply) flow=1 = 19 consecutive
# monthly obs 2025-01..2026-07 (EUR 7.4M..27.1M), flow=2 = only 2 sporadic
# obs (EUR 169k / 34k).
COMEXT_FLOW = "1"
COMEXT_INDICATOR = "VALUE_IN_EUROS"

# HS6 photosensitive-semiconductor family (PV cells & LEDs) across the HS
# revision break: 854140 (HS2017) data ends 2021-12; HS2022 split carries 2022+.
# Since-windows fixed to the measured start of each revision (2026-10-07).
COMEXT_PRODUCTS: list[dict] = [
    {"product": "854140", "since": "2018-01"},
    {"product": "854141", "since": "2022-01"},
    {"product": "854142", "since": "2022-01"},
    {"product": "854143", "since": "2022-01"},
    {"product": "854149", "since": "2022-01"},
]

SDMX_NS = {
    "m": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message",
    "g": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic",
}

SDMX_HDRS = {
    "Accept": "application/vnd.sdmx.genericdata+xml;version=2.1",
    "User-Agent": "fd-industry-data/eurostat-api (batch data pipeline)",
}


def _comext_url(product: str, since: str) -> str:
    key = f"{COMEXT_DATAFLOW}/M.{COMEXT_REPORTER}.{COMEXT_PARTNER}.{product}.{COMEXT_FLOW}.{COMEXT_INDICATOR}"
    return f"{COMEXT_BASE_URL}/{key}?startPeriod={since}"


async def _get_sdmx(url: str, proxy: str | None) -> tuple[int | None, bytes]:
    async with FetcherSession(
        impersonate="chrome120", timeout=HTTP_TIMEOUT, verify=False, proxy=proxy
    ) as session:
        resp = await session.get(url, headers=SDMX_HDRS)
        return resp.status, resp.body or b""


async def _fetch_sdmx(product: str, url: str) -> str:
    """One comext fetch honoring the sticky direct/proxy mode; returns XML text."""
    global NET_MODE
    modes = ["direct", "proxy"] if NET_MODE in (None, "direct") else ["proxy"]
    status, body = None, b""
    for mode in modes:
        if mode == "proxy" and not _proxy_port_open():
            logger.error("[comext] fallback proxy 127.0.0.1:7890 is not open")
            break
        try:
            status, body = await _get_sdmx(url, None if mode == "direct" else FALLBACK_PROXY)
        except Exception as exc:  # connection layer only
            logger.warning("[comext/%s] %s connection error: %s", product, mode, exc)
            continue
        if status == 200 and body:
            NET_MODE = mode
            try:
                return body.decode("utf-8")
            except UnicodeDecodeError:
                return body.decode("utf-8", errors="replace")
        logger.warning(
            "[comext/%s] %s HTTP %s (%d bytes) -> log-and-skip, no retry",
            product, mode, status, len(body),
        )
        NET_MODE = mode
        break
    raise RuntimeError(f"[comext] fetch failed (HTTP {status}) for {url}")


def _parse_sdmx_generic(xml_text: str, url: str, scraped_at: str) -> list[dict]:
    """Flatten an SDMX-ML 2.1 GenericData message into row dicts.

    The comext dataflow carries six series-key dimensions (freq, reporter,
    partner, product, indicators, flow) observed on TIME_PERIOD. An empty set
    is a valid envelope with zero Series — the caller asserts non-empty.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("[comext] non-XML response from %s: %s", url, exc)
        return []
    if root.tag != f"{{{SDMX_NS['m']}}}GenericData":
        logger.warning("[comext] unexpected payload root %s from %s", root.tag, url)
        return []
    rows: list[dict] = []
    for series in root.findall(f".//{{{SDMX_NS['g']}}}Series"):
        key = {
            v.get("id"): v.get("value", "")
            for v in series.findall(f"./{{{SDMX_NS['g']}}}SeriesKey/{{{SDMX_NS['g']}}}Value")
        }
        for obs in series.findall(f"./{{{SDMX_NS['g']}}}Obs"):
            period_el = obs.find(f"./{{{SDMX_NS['g']}}}ObsDimension")
            value_el = obs.find(f"./{{{SDMX_NS['g']}}}ObsValue")
            if period_el is None or value_el is None:
                continue
            raw = value_el.get("value")
            if raw is None or raw in ("", "NaN"):
                continue  # missing observation -> skip, never fabricate
            try:
                value = float(raw)
            except ValueError:
                logger.warning("[comext] non-numeric ObsValue %r, skipped", raw)
                continue
            rows.append({
                "dataset": COMEXT_DATAFLOW,
                "geo": key.get("reporter", COMEXT_REPORTER),
                "freq": key.get("freq", "M"),
                "unit": key.get("indicators", COMEXT_INDICATOR),
                "indic": COMEXT_INDICATOR,
                "s_adj": "",
                "nace_r2": "",
                "p_adj": "",
                "partner": key.get("partner", COMEXT_PARTNER),
                "product": key.get("product", ""),
                "flow": key.get("flow", COMEXT_FLOW),
                "period": period_el.get("value", ""),
                # EUR trade amount, distinct from the index-semantics `value`
                # column of the JSON-stat entry points (concept split: manifest
                # binds trade_value -> economic.trade_value, unit EUR).
                "trade_value": value,
                "scraped_at": scraped_at,
                "source_url": url,
            })
    return rows


async def _run_comext(limit: int) -> list[dict]:
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    for item in COMEXT_PRODUCTS:
        url = _comext_url(item["product"], item["since"])
        xml_text = await _fetch_sdmx(item["product"], url)
        got = _assert_nonempty(
            _parse_sdmx_generic(xml_text, url, scraped_at), f"comext/{item['product']}", url
        )
        logger.info("[comext/%s] parsed %d rows from %s", item["product"], len(got), url)
        rows.extend(got)
    return rows[:limit]


def run_eurostat_comext(limit: int = 500) -> list[dict]:
    """EU-China monthly trade, HS6 photosensitive-semiconductor family.

    DS-045409 via api/comext/dissemination (the /statistics segment 404s here).
    EU imports (flow=1 = the China→EU export view; flow=2 is EU exports),
    VALUE_IN_EUROS, reporter EU27_2020, partner CN. Rows carry the EUR amount
    in `trade_value` (trade-amount semantics, concept economic.trade_value —
    not the index-semantics `value` column of the other four entry points).
    Frozen anchor: 854142, 2025-01 = 3917107 (EUR, EU imports from China;
    verified sample URL — the previously frozen 95058 was the flow=2 EU-export
    value and its series was re-pointed to flow=1 on 2026-10-07).
    """
    return asyncio.run(_run_comext(max(0, int(limit))))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    for row in run_eurostat_api(limit=5):
        print(json.dumps(row, ensure_ascii=False))
