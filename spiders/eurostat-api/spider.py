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
- run_eurostat_hicp    prc_hicp_midx HICP catering services (CP111) price index,
                       unit=I15 (index 2015=100), EU27_2020 aggregate, since
                       1996-01 (first actual values 2000-12; restaurant-vertical
                       wave 0 EU leg. NOTE: prc_hicp_inx is dead upstream, HTTP
                       404 "not available for dissemination" (re-measured
                       2026-11-07) — never use it; prc_hicp_midx is the live
                       index-level dataset and I15 is the 2015=100 unit code
                       (I05/I96 are older bases).)
- run_eurostat_i56     sts_setu_m NACE I56 (food & beverage service activities)
                       turnover index, indic_bt=NETTUR, s_adj=SCA, unit=I21
                       (2021=100), EU27_2020, since 2000-01 — restaurant-vertical
                       wave 1 EU leg (user adjudication 2026-11-07 "补 NACE I56
                       营业额指数"). This CORRECTS the wave-0 claim above that the
                       STS services-turnover family is wholly dead: sts_tsur_m is
                       404, but sts_setu_m serves I56 (319 values 2000-01..2026-07,
                       no gaps, re-measured 2026-11-08). Price index ≠ turnover
                       index, so it never merges with the HICP series.
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
- run_eurostat_c20 / run_eurostat_bsci / run_eurostat_apri / run_eurostat_hicp
- run_eurostat_comext

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


def _wave_c_rows(limit: int) -> list[dict]:
    """Rows from the four index-shaped Wave-C extensions, via their own entries.

    Delegating to the declared commands (rather than only reusing their CFGs)
    keeps the manifest honest: a declared command is now genuinely invoked by
    the platform, and each one's non-empty assertion stays in force.

    Each is called by explicit name in its own try block — not looped over a
    tuple of functions — because the admission gate resolves the entry's call
    graph statically and a dynamic call target is invisible to it.

    The four outputs are round-robin interleaved (not concatenated): the
    caller interleaves this list against the base-dataset bucket and truncates
    to ``limit``, so a concatenation let the first extension (c20) own the
    whole wave-c share — at the scheduled ``--limit 100`` only the first ~50
    wave-c rows survive, which starved bsci/apri entirely and would never
    store a single hicp row (measured 2026-11-07, run 4785: 100 rows, all
    base + c20-as-sts_inpr_m).
    """
    buckets: list[list[dict]] = []
    n = max(limit, 1)
    try:
        buckets.append(run_eurostat_c20(limit=n))
    except Exception as e:  # noqa: BLE001 - one dataset must not kill the run
        logger.warning("eurostat-api: c20 failed; skipping: %s", e)
    try:
        buckets.append(run_eurostat_bsci(limit=n))
    except Exception as e:  # noqa: BLE001
        logger.warning("eurostat-api: bsci failed; skipping: %s", e)
    try:
        buckets.append(run_eurostat_apri(limit=n))
    except Exception as e:  # noqa: BLE001
        logger.warning("eurostat-api: apri failed; skipping: %s", e)
    try:
        buckets.append(run_eurostat_hicp(limit=n))
    except Exception as e:  # noqa: BLE001
        logger.warning("eurostat-api: hicp failed; skipping: %s", e)
    try:
        # 餐饮垂直线 EU leg（NACE I56 营业额指数）：同为指数形状，一并进 wave-c 轮转。
        # 声明了命令却无人调用会被 conformance 判 declared-command-reachability。
        buckets.append(run_eurostat_i56(limit=n))
    except Exception as e:  # noqa: BLE001
        logger.warning("eurostat-api: i56 failed; skipping: %s", e)
    return _interleave(buckets)


async def _run(limit: int) -> list[dict]:
    """Fetch every entry dataset and interleave them round-robin.

    Sequential concatenation let the first dataset swallow the whole budget:
    the CronJob renders ``--limit 100`` and ``sts_inpr_m`` alone holds 840
    rows, so ``ei_bsco_m`` and ``apri_pi_outq`` came back empty on every
    scheduled run. Interleaving gives each dataset a turn, so a small limit
    still spans them all (same fix as mohurd-yearbook).
    """
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    buckets: list[list[dict]] = []
    for cfg in DATASETS:
        url = _build_url(cfg)
        doc = await _fetch_doc(url, cfg["dataset"])
        if doc is None:
            continue
        got = _jsonstat_rows(doc, cfg["colmap"], cfg["dataset"], url, scraped_at)
        logger.info("[%s] parsed %d rows from %s", cfg["dataset"], len(got), url)
        buckets.append(got)
    rows = _interleave(buckets)
    return rows[:limit]


def _interleave(buckets: list[list[dict]]) -> list[dict]:
    """Round-robin rows across datasets so no dataset starves another."""
    out: list[dict] = []
    for i in range(max((len(b) for b in buckets), default=0)):
        for b in buckets:
            if i < len(b):
                out.append(b[i])
    return out


def run_eurostat_api(limit: int = 100) -> list[dict]:
    """fd-runner entry point. Returns at most `limit` rows.

    Covers the three base datasets plus the four Wave-C extensions (C20 /
    BSCI / APRI / comext). The extensions used to live only behind their own
    declared commands, which no platform surface invoked — they had goldens
    and zero production rows. comext is appended last and keeps its own
    ``trade_value`` column: it is a trade amount, not an index, so it must
    not be folded into the index-shaped ``value`` rows.
    """
    limit = max(0, int(limit))
    buckets = [asyncio.run(_run(limit)), _wave_c_rows(limit)]
    if limit and sum(len(b) for b in buckets) < limit:
        try:
            buckets.append(run_eurostat_comext(
                limit=limit - sum(len(b) for b in buckets)))
        except Exception as e:  # noqa: BLE001 - one dataset must not kill the run
            logger.warning("eurostat-api: comext path failed; skipping: %s", e)
    rows = _interleave([b for b in buckets if b])
    return rows[:limit] if limit else rows


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

HICP_CFG: dict = {
    # prc_hicp_inx is dead upstream (HTTP 404 "not available for dissemination",
    # re-measured 2026-11-07 regardless of parameters) — prc_hicp_midx is the
    # live index-level HICP dataset. unit I15 = index 2015=100 (dimension also
    # carries I05/I96 older bases); coicop CP111 = Catering services (CP11 =
    # Restaurants and hotels). Restaurant-vertical wave 0 EU leg: replaces the
    # dead STS services-turnover view with the HICP price-index view.
    "dataset": "prc_hicp_midx",
    "dims": {"freq": "M", "unit": "I15", "coicop": "CP111"},
    "geos": ["EU27_2020"],
    # full history: header window starts 1996-01, first actual value 2000-12
    # (null cells omitted upstream), 301 monthly values through 2025-12, no gaps.
    "since": "1996-01",
    "colmap": {"indic": "coicop", "nace_r2": None, "s_adj": None, "p_adj": None},
}


SETU_I56_CFG: dict = {
    # Restaurant-vertical wave 1 EU leg (user adjudication 2026-11-07: "补 NACE I56
    # 营业额指数"). sts_setu_m = Turnover in services, monthly. LIVE (re-measured
    # 2026-11-08): NETTUR + SCA + I21 + EU27_2020 + I56 = 319 values 2000-01..2026-07,
    # no gaps; 2015-01 = 88.7, 2026-07 = 180.5 (2021=100 base).
    # This corrects the wave-0 note that called the whole STS services-turnover
    # family dead: sts_tsur_m IS 404, but sts_setu_m serves I56 fine. Both
    # s_adj=CA and SCA are non-empty; SCA (calendar+seasonally adjusted) is used
    # so month-on-month comparisons are meaningful.
    "dataset": "sts_setu_m",
    "dims": {"freq": "M", "indic_bt": "NETTUR", "nace_r2": "I56",
             "s_adj": "SCA", "unit": "I21"},
    "geos": ["EU27_2020"],
    "since": "2000-01",
    "colmap": {"indic": "indic_bt", "nace_r2": "nace_r2", "s_adj": "s_adj", "p_adj": None},
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


def run_eurostat_hicp(limit: int = 100) -> list[dict]:
    """HICP catering services (CP111) index, I15 2015=100 (EU27_2020, monthly).

    prc_hicp_inx is dead upstream (404) — the live index-level dataset is
    prc_hicp_midx. Full history since 1996-01; first actual value 2000-12.
    Frozen anchors: 2000-12 = 67.72 (first value), 2015-01 = 99.28 (I15).
    """
    return asyncio.run(_run_single(HICP_CFG, max(0, int(limit))))


def run_eurostat_i56(limit: int = 500) -> list[dict]:
    """EU27 NACE I56 (food & beverage service activities) turnover index, monthly.

    sts_setu_m / NETTUR / SCA / I21 (2021=100), full history since 2000-01.
    Frozen anchors (SCA variant, measured 2026-11-08): 2000-01 = 82.9,
    2026-07 = 179.5 (last value), 319 monthly values with no gaps.
    Restaurant-vertical wave 1 EU leg.
    """
    return asyncio.run(_run_single(SETU_I56_CFG, max(0, int(limit))))


# --------------------------------------------------------------------------
# hotel-vertical wave 0 — accommodation (NACE I55) + tourism nights.
# Every combo below was measured live on 2026-11-08; the two NEGATIVE results
# are recorded too, because they explain why this line has no price index:
#   * sts_setu_m nace_r2=I551 (hotels only) -> HTTP 200 with 0 values
#   * prc_hicp_midx coicop=CP11 / CP112     -> 404 "not available for
#     dissemination" (only CP111, catering, is served)
# Never re-add a CP11/CP112 accommodation price series without re-measuring.
# --------------------------------------------------------------------------

SETU_I55_CFG: dict = {
    # Accommodation (NACE I55) turnover index. LIVE, measured 2026-11-08:
    # NETTUR + SCA + I21 + EU27_2020 + I55 = 139 values 2015-01..2026-07.
    # Note the depth difference against I56 (319 values from 2000-01) in the
    # SAME dataset: the I55 branch starts in 2015, so never copy I56's
    # "since 2000-01, no gaps" wording into anything about I55.
    "dataset": "sts_setu_m",
    "dims": {"freq": "M", "indic_bt": "NETTUR", "nace_r2": "I55",
             "s_adj": "SCA", "unit": "I21"},
    "geos": ["EU27_2020"],
    "since": "2000-01",
    "colmap": {"indic": "indic_bt", "nace_r2": "nace_r2", "s_adj": "s_adj", "p_adj": None},
}

TOUR_OCC_M_CFG: dict = {
    # Nights spent at tourist accommodation establishments, monthly.
    # c_resid x unit are both list-valued → four series in one request:
    # TOTAL/NR, TOTAL/PCH_SM, FOR/NR, FOR/PCH_SM (188 values each,
    # 2011-01..2026-08). c_resid maps onto the `indic` column because it is the
    # series-defining dimension here (same convention as HICP's coicop).
    "dataset": "tour_occ_nim",
    "dims": {"freq": "M", "c_resid": ["TOTAL", "FOR"], "unit": ["NR", "PCH_SM"],
             "nace_r2": "I551-I553"},
    "geos": ["EU27_2020"],
    "since": "1990-01",
    "colmap": {"indic": "c_resid", "nace_r2": "nace_r2", "s_adj": None, "p_adj": None},
}

TOUR_OCC_A_CFG: dict = {
    # Nights spent, annual. Header says 1990..2025 but the EU27_2020 aggregate
    # carries only 21 non-null values (early years empty) — gaps stay gaps.
    "dataset": "tour_occ_ninat",
    "dims": {"freq": "A", "c_resid": "TOTAL", "unit": "NR", "nace_r2": "I551"},
    "geos": ["EU27_2020"],
    "since": "1990-01",
    "colmap": {"indic": "c_resid", "nace_r2": "nace_r2", "s_adj": None, "p_adj": None},
}

TOUR_CAP_CFG: dict = {
    # Accommodation capacity in bed places, annual; 21 values 2005..2025
    # (2005 is the first year the EU27 aggregate publishes a value).
    "dataset": "tour_cap_nat",
    "dims": {"freq": "A", "accomunit": "BEDPL", "unit": "NR", "nace_r2": "I551"},
    "geos": ["EU27_2020"],
    "since": "1990-01",
    "colmap": {"indic": "accomunit", "nace_r2": "nace_r2", "s_adj": None, "p_adj": None},
}


def run_eurostat_i55(limit: int = 500) -> list[dict]:
    """EU27 NACE I55 (accommodation) turnover index, monthly, 2021=100.

    sts_setu_m / NETTUR / SCA / I21, 139 values 2015-01..2026-07 (measured
    2026-11-08). The hotels-only branch I551 returns 0 values upstream, so this
    is the accommodation aggregate — never label it "hotel turnover".
    """
    return asyncio.run(_run_single(SETU_I55_CFG, max(0, int(limit))))


def run_eurostat_tour_occ(limit: int = 1000) -> list[dict]:
    """EU27 nights spent at tourist accommodation establishments, monthly.

    tour_occ_nim, nace_r2=I551-I553, four series (TOTAL/FOR x NR/PCH_SM),
    188 values each 2011-01..2026-08 (measured 2026-11-08).
    """
    return asyncio.run(_run_single(TOUR_OCC_M_CFG, max(0, int(limit))))


def run_eurostat_tour_annual(limit: int = 200) -> list[dict]:
    """EU27 nights spent, annual (tour_occ_ninat, TOTAL/NR/I551).

    21 non-null values 1990..2025 — the header window is wider than the data;
    missing years are omitted, never interpolated or zero-filled.
    """
    return asyncio.run(_run_single(TOUR_OCC_A_CFG, max(0, int(limit))))


def run_eurostat_tour_cap(limit: int = 200) -> list[dict]:
    """EU27 accommodation capacity in bed places, annual (tour_cap_nat).

    accomunit=BEDPL, nace_r2=I551, 21 values 2005..2025 (measured 2026-11-08).
    """
    return asyncio.run(_run_single(TOUR_CAP_CFG, max(0, int(limit))))


FLOWER_AREA_CFG: dict = {
    # 花卉垂直线（add-flower-vertical 3.3）：apro_cpsh1 = "Crop production in EU
    # standard humidity"。实测 2026-11-08（直连 HTTP 200）：
    # * 花卉作物码 = N0000「Flowers and ornamental plants (excluding nurseries)」；
    # * 该作物**只有 MAR_THS_HA（Main area · 千公顷）有值**——AR_THS_HA /
    #   HPRD_HUMD_EU_THS_T / YLD_HUMD_EU_T_HA 三种口径查询均返回 0 值（先按
    #   产量口径试过才改判面积），所以这里只能取主面积，绝不能标成「产量」；
    # * 覆盖 41 geo × 12 年（2015–2026），316 个非零值；EU 合计 2021 = 72.48 千公顷。
    # geo 取花卉生产主要国 + EU 口径合计：EU27_2020 是「欧盟 27 国」现役口径，
    # EU28/EU 是历史口径（同一年的值可能相同），三者不可相加、只作对照。
    "dataset": "apro_cpsh1",
    "dims": {"freq": "A", "crops": "N0000", "strucpro": "MAR_THS_HA"},
    "geos": ["EU27_2020", "NL", "IT", "DE", "FR", "ES", "PL", "BE"],
    "since": "2015",
    # crops/strucpro 是这一数据集特有的维度：其它入口的 colmap 没有它们，
    # 所以只能在本条 cfg 里声明（_jsonstat_rows 按 colmap 逐键取维度值）。
    "colmap": {"indic": None, "nace_r2": None, "s_adj": None, "p_adj": None,
               "crops": "crops", "strucpro": "strucpro"},
}


def run_eurostat_flower_area(limit: int = 200) -> list[dict]:
    """EU 花卉（N0000）主面积，年频，千公顷（apro_cpsh1 / MAR_THS_HA）。

    8 个 geo × 12 年 ≈ 96 行，所以本命令自带 limit；它**不参与
    ``run_eurostat_api`` 的 --limit 100 轮转**（轮转会把 96 行截成头 10 行，
    存成半截序列比没有更坏）。抽取器按酒店线先例直接调用本函数。
    """
    return asyncio.run(_run_single(FLOWER_AREA_CFG, max(0, int(limit))))


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
