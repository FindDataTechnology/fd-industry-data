"""
NBS GDP & Macroeconomic Data Spider — Enhanced version with multiple endpoints.

Architecture:
  1. Primary: Direct FetcherSession against NBS easyquery API (browser impersonation)
  2. Fallback: akshare macro_china_* functions (wraps NBS data)
  3. Spider class: Framework-compatible interface for Scrapling ecosystem

Anti-bot measures:
  - Browser impersonation (chrome via curl_cffi)
  - Proper Referer + Accept headers
  - Rate limiting (2s delay)
  - Timestamp cache-busting (k1 param)

Outputs:
  - data/nbs_macro.db    (SQLite, table: nbs_macro)
  - output/nbs_gdp.json  (GDP-only records)
  - output/nbs_macro.json (All fetched indicators)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scrapling.fetchers import FetcherSession
from scrapling.spiders import Spider, Request, Response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("nbs_macro")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "nbs_macro.db"
JSON_GDP_PATH = OUTPUT_DIR / "nbs_gdp.json"
JSON_MACRO_PATH = OUTPUT_DIR / "nbs_macro.json"

NBS_BASE_URL = "https://data.stats.gov.cn/easyquery.htm"
NBS_REFERER = "https://data.stats.gov.cn/easyquery.htm?cn=A01"

INDICATORS: dict[str, dict[str, str]] = {
    "gdp_quarterly": {"dbcode": "hgjd", "zbcode": "A0201", "freq": "Q"},
    "gdp_annual": {"dbcode": "hgnd", "zbcode": "A0201", "freq": "A"},
    "cpi_monthly": {"dbcode": "hgyd", "zbcode": "A0901", "freq": "M"},
    "ppi_monthly": {"dbcode": "hgyd", "zbcode": "A0902", "freq": "M"},
    "pmi_monthly": {"dbcode": "hgyd", "zbcode": "A0M01", "freq": "M"},
}

_QUARTER_RE = re.compile(r"(\d{4})年第(\d)季度")
_ISO_RE = re.compile(r"(\d{4})-(\d{2})")
_YEAR_RE = re.compile(r"(\d{4})年")

_UNIT_MAP: dict[str, str] = {
    "gdp_quarterly": "亿元",
    "gdp_annual": "亿元",
    "cpi_monthly": "%",
    "ppi_monthly": "%",
    "pmi_monthly": "%",
}

_AKSHARE_FUNC_MAP: dict[str, str] = {
    "gdp_quarterly": "macro_china_gdp_yearly",
    "cpi_monthly": "macro_china_cpi_yearly",
    "ppi_monthly": "macro_china_ppi_yearly",
    "pmi_monthly": "macro_china_pmi_yearly",
}


def _parse_period(raw: str, freq: str = "Q") -> str:
    raw = str(raw).strip()
    if freq == "Q":
        m = _QUARTER_RE.match(raw)
        if m:
            return f"{m.group(1)}Q{m.group(2)}"
        m = _ISO_RE.match(raw)
        if m:
            return f"{m.group(1)}Q{((int(m.group(2)) - 1) // 3 + 1)}"
    elif freq == "A":
        m = _YEAR_RE.match(raw)
        if m:
            return m.group(1)
    elif freq == "M":
        m = _ISO_RE.match(raw)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
    return raw


def _to_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        f = float(val)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _build_nbs_url(
    dbcode: str,
    zbcode: str,
    start_year: int = 2010,
) -> str:
    freq_suffix_map = {"hgjd": "A", "hgyd": "01", "hgnd": ""}
    suffix = freq_suffix_map.get(dbcode, "")
    start_period = f"{start_year}{suffix}" if suffix else str(start_year)

    wds = "[]"
    dfwds = json.dumps(
        [
            {"wdcode": "zb", "valuecode": zbcode},
            {"wdcode": "sj", "valuecode": start_period},
        ],
        separators=(",", ":"),
    )
    k1 = str(int(time.time() * 1000))
    return (
        f"{NBS_BASE_URL}?m=QueryData&dbcode={dbcode}"
        f"&rowcode=zb&colcode=sj&wds={wds}&dfwds={dfwds}&k1={k1}"
    )


def _parse_nbs_response(text: str, indicator_name: str, freq: str = "Q") -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("JSON decode error: %s", e)
        return []

    if data.get("returncode") != 200:
        logger.warning("NBS API returncode=%s", data.get("returncode"))
        return []

    returndata = data.get("returndata") or {}
    datanodes = returndata.get("datanodes") or []
    wdnodes = returndata.get("wdnodes") or []

    sj_map: dict[str, str] = {}
    zb_map: dict[str, str] = {}
    for wdnode in wdnodes:
        wdcode = wdnode.get("wdcode", "")
        for entry in wdnode.get("nodes") or []:
            code = entry.get("code", "")
            name = entry.get("name", "")
            if wdcode == "sj":
                sj_map[code] = name
            elif wdcode == "zb":
                zb_map[code] = name

    items: list[dict[str, Any]] = []
    for node in datanodes:
        data_obj = node.get("data") or {}
        if not data_obj.get("hasdata"):
            continue
        value_str = data_obj.get("strdata") or data_obj.get("data")
        if value_str is None or value_str == "":
            continue

        value = _to_float(value_str)
        if value is None:
            continue

        code = node.get("code", "")
        parts = code.split(".")
        if len(parts) < 2:
            continue

        period_raw = parts[-1]
        zb_code = parts[0]
        period = _parse_period(sj_map.get(period_raw, period_raw), freq)
        ind_name = zb_map.get(zb_code, indicator_name)

        items.append(
            {
                "period": period,
                "value": value,
                "indicator_code": zb_code,
                "indicator_name": ind_name,
                "indicator_type": indicator_name,
                "unit": _UNIT_MAP.get(indicator_name, ""),
                "source": "NBS",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    return items


def _fetch_via_akshare(indicator: str = "gdp_quarterly", start_year: int = 2010) -> list[dict[str, Any]]:
    logger.info("Falling back to akshare for %s", indicator)
    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare not installed")
        return []

    func_name = _AKSHARE_FUNC_MAP.get(indicator)
    if not func_name:
        logger.error("No akshare function for %s", indicator)
        return []

    func = getattr(ak, func_name, None)
    if func is None:
        logger.error("akshare.%s not found", func_name)
        return []

    try:
        df = func()
    except Exception as e:
        logger.error("akshare %s failed: %s", func_name, e)
        return []

    freq = INDICATORS.get(indicator, {}).get("freq", "Q")
    zbcode = INDICATORS.get(indicator, {}).get("zbcode", "")
    unit = _UNIT_MAP.get(indicator, "")

    items: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        date_str = str(row.get("日期", ""))
        value = _to_float(row.get("今值"))
        if value is None:
            continue

        period = _parse_period(date_str, freq)
        year_match = re.match(r"(\d{4})", period)
        if year_match and int(year_match.group(1)) < start_year:
            continue

        items.append(
            {
                "period": period,
                "value": value,
                "indicator_code": zbcode,
                "indicator_name": indicator,
                "indicator_type": indicator,
                "unit": unit,
                "source": "akshare",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    return items


async def _fetch_nbs_direct(indicator: str, start_year: int = 2010) -> list[dict[str, Any]]:
    """Fetch from NBS API using FetcherSession with browser impersonation."""
    config = INDICATORS.get(indicator)
    if not config:
        return []

    url = _build_nbs_url(config["dbcode"], config["zbcode"], start_year)
    logger.info("Fetching NBS API: %s", url[:120])

    session = FetcherSession(impersonate="chrome")
    try:
        response = await session.fetch(
            url,
            headers={
                "Referer": NBS_REFERER,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
    except Exception as e:
        logger.warning("NBS direct fetch failed: %s", e)
        return []

    if response.status != 200:
        logger.warning("NBS API status=%s", response.status)
        return []

    if not response.text or len(response.text) < 100:
        logger.warning("NBS API response too short (%d bytes)", len(response.text or ""))
        return []

    return _parse_nbs_response(response.text, indicator, config["freq"])


def _fetch_indicator(indicator: str, start_year: int = 2010) -> list[dict[str, Any]]:
    """Fetch a single indicator: try NBS direct, then akshare fallback."""
    items: list[dict[str, Any]] = []

    try:
        items = asyncio.run(_fetch_nbs_direct(indicator, start_year))
    except Exception as e:
        logger.warning("NBS direct fetch raised: %s", e)

    if items:
        logger.info("NBS API returned %d records for %s", len(items), indicator)
        return items

    logger.warning("NBS API returned no data for %s, using akshare fallback", indicator)
    items = _fetch_via_akshare(indicator, start_year)
    if items:
        logger.info("akshare returned %d records for %s", len(items), indicator)

    return items


class NbsMacroSpider(Spider):
    """Scrapling Spider framework wrapper for NBS macro data."""

    name = "nbs_macro"
    start_urls: list[str] = []
    allowed_domains = {"data.stats.gov.cn"}
    concurrent_requests = 1
    download_delay = 2

    indicators: list[str] = ["gdp_quarterly"]
    start_year: int = 2010

    def configure_sessions(self, manager):
        manager.add(
            "default",
            FetcherSession(impersonate="chrome"),
            default=True,
        )

    async def start_requests(self):
        for indicator in self.indicators:
            config = INDICATORS.get(indicator, {})
            if not config:
                continue
            url = _build_nbs_url(config["dbcode"], config["zbcode"], self.start_year)
            yield Request(
                url=url,
                callback=self.parse,
                meta={"indicator": indicator, "freq": config["freq"]},
            )

    async def parse(self, response: Response):
        indicator = response.meta.get("indicator", "gdp_quarterly")
        freq = response.meta.get("freq", "Q")
        items: list[dict[str, Any]] = []

        if response.status == 200 and response.text:
            try:
                items = _parse_nbs_response(response.text, indicator, freq)
            except Exception as e:
                logger.warning("Parse failed for %s: %s", indicator, e)

        if not items:
            logger.warning("Spider: NBS no data for %s, akshare fallback", indicator)
            items = _fetch_via_akshare(indicator, self.start_year)

        for item in items:
            yield item

        logger.info("Spider yielded %d records for %s", len(items), indicator)


def save_to_sqlite(items: list[dict[str, Any]], db_path: Path = DB_PATH) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS nbs_macro (
            period          TEXT,
            indicator_type  TEXT,
            value           REAL,
            indicator_code  TEXT,
            indicator_name  TEXT,
            unit            TEXT,
            source          TEXT,
            fetched_at      TEXT,
            PRIMARY KEY (period, indicator_type)
        )
        """
    )
    inserted = 0
    for item in items:
        try:
            cur.execute(
                """
                INSERT OR REPLACE INTO nbs_macro
                (period, indicator_type, value, indicator_code, indicator_name, unit, source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["period"],
                    item["indicator_type"],
                    item["value"],
                    item.get("indicator_code", ""),
                    item.get("indicator_name", ""),
                    item.get("unit", ""),
                    item.get("source", ""),
                    item.get("fetched_at", ""),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    conn.close()
    return inserted


def save_to_json(items: list[dict[str, Any]], json_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def get_macro_data(
    indicators: list[str] | None = None,
    start_year: int = 2010,
) -> list[dict[str, Any]]:
    """Fetch macro indicators from NBS (with akshare fallback).

    Args:
        indicators: List of indicator keys (default: ["gdp_quarterly"]).
            Available: gdp_quarterly, gdp_annual, cpi_monthly, ppi_monthly, pmi_monthly.
        start_year: Earliest year to include (default: 2010).

    Returns:
        List of dicts with keys: period, value, indicator_code, indicator_name,
        indicator_type, unit, source, fetched_at.
    """
    if indicators is None:
        indicators = ["gdp_quarterly"]

    all_items: list[dict[str, Any]] = []
    for indicator in indicators:
        items = _fetch_indicator(indicator, start_year)
        all_items.extend(items)

    if all_items:
        gdp_items = [i for i in all_items if "gdp" in i.get("indicator_type", "")]
        if gdp_items:
            save_to_json(gdp_items, JSON_GDP_PATH)
        save_to_json(all_items, JSON_MACRO_PATH)
        n_sqlite = save_to_sqlite(all_items)
        logger.info(
            "Saved %d records: %d to SQLite (%s), JSON GDP (%s), JSON all (%s)",
            len(all_items),
            n_sqlite,
            DB_PATH,
            JSON_GDP_PATH,
            JSON_MACRO_PATH,
        )
    else:
        logger.error("No macro data obtained from any source")

    return all_items


def get_gdp_quarterly(start_year: int = 2010) -> list[dict[str, Any]]:
    """Fetch quarterly GDP data from NBS (with akshare fallback)."""
    return get_macro_data(["gdp_quarterly"], start_year)


if __name__ == "__main__":
    results = get_macro_data(["gdp_quarterly", "cpi_monthly", "ppi_monthly"])
    print(f"\n{'=' * 70}")
    print(f"Total records: {len(results)}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (GDP): {JSON_GDP_PATH}")
    print(f"JSON (All): {JSON_MACRO_PATH}")
    print(f"{'=' * 70}")
    for indicator in ["gdp_quarterly", "cpi_monthly", "ppi_monthly"]:
        ind_items = [r for r in results if r.get("indicator_type") == indicator]
        if ind_items:
            print(f"\n{indicator.upper()} ({len(ind_items)} records):")
            for r in sorted(ind_items, key=lambda x: x.get("period", ""))[-6:]:
                print(f"  {r['period']:>12s}  {r['value']:>12,.2f}  {r.get('unit', '')}  [{r.get('source', '')}]")
