#!/usr/bin/env python3
"""
National Bureau of Statistics (NBS) Spider - 国家统计局数据爬虫

Target: https://www.stats.gov.cn/ and https://data.stats.gov.cn/
Data Coverage:
  - GDP data (quarterly/annual)
  - CPI/PPI price indices
  - Industrial production statistics
  - Population and employment data
  - Economic indicators by province

Architecture:
  - Primary: NBS easyquery API with browser impersonation
  - Anti-bot: Proper headers, rate limiting, cache-busting
  - Output: SQLite DB + JSON export
  - Special: Multiple indicator categories with fallback to akshare
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
logger = logging.getLogger("nbs_stats")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "nbs_stats.db"

NBS_BASE_URL = "https://data.stats.gov.cn/easyquery.htm"
NBS_REFERER = "https://data.stats.gov.cn/easyquery.htm?cn=A01"

INDICATOR_CATEGORIES = {
    "gdp": {
        "quarterly": {"dbcode": "hgjd", "zbcode": "A0201", "freq": "Q"},
        "annual": {"dbcode": "hgnd", "zbcode": "A0201", "freq": "A"},
        "by_province": {"dbcode": "fsjd", "zbcode": "A0201", "freq": "Q"},
    },
    "price_indices": {
        "cpi_monthly": {"dbcode": "hgyd", "zbcode": "A0901", "freq": "M"},
        "ppi_monthly": {"dbcode": "hgyd", "zbcode": "A0902", "freq": "M"},
    },
    "industrial": {
        "production": {"dbcode": "hgyd", "zbcode": "A0202", "freq": "M"},
        "pmi": {"dbcode": "hgyd", "zbcode": "A0M01", "freq": "M"},
    },
    "population": {
        "annual": {"dbcode": "hgnd", "zbcode": "A0301", "freq": "A"},
    },
    "employment": {
        "urban": {"dbcode": "hgyd", "zbcode": "A0302", "freq": "M"},
    },
}

_UNIT_MAP = {
    "gdp": "亿元",
    "cpi": "%",
    "ppi": "%",
    "production": "%",
    "pmi": "%",
    "population": "万人",
    "employment": "%",
}

_QUARTER_RE = re.compile(r"(\d{4})年第(\d)季度")
_ISO_RE = re.compile(r"(\d{4})-(\d{2})")
_YEAR_RE = re.compile(r"(\d{4})年")


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


def _build_nbs_url(dbcode: str, zbcode: str, start_period: str) -> str:
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


def _parse_nbs_response(
    text: str, category: str, indicator: str, freq: str = "Q"
) -> list[dict[str, Any]]:
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
        ind_name = zb_map.get(zb_code, indicator)

        items.append(
            {
                "period": period,
                "value": value,
                "indicator_code": zb_code,
                "indicator_name": ind_name,
                "category": category,
                "indicator_type": indicator,
                "unit": _UNIT_MAP.get(category, ""),
                "frequency": freq,
                "source": "NBS",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    return items


async def _fetch_nbs_indicator(
    category: str, indicator: str, start_year: int = 2010
) -> list[dict[str, Any]]:
    config = INDICATOR_CATEGORIES.get(category, {}).get(indicator)
    if not config:
        logger.warning("No config for %s/%s", category, indicator)
        return []

    freq = config["freq"]
    freq_suffix_map = {"hgjd": "A", "hgyd": "01", "hgnd": "", "fsjd": "A"}
    suffix = freq_suffix_map.get(config["dbcode"], "")
    start_period = f"{start_year}{suffix}" if suffix else str(start_year)

    url = _build_nbs_url(config["dbcode"], config["zbcode"], start_period)
    logger.info("Fetching NBS %s/%s: %s", category, indicator, url[:120])

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
        logger.warning("NBS fetch failed: %s", e)
        return []

    if response.status != 200:
        logger.warning("NBS API status=%s", response.status)
        return []

    if not response.text or len(response.text) < 100:
        logger.warning("NBS API response too short")
        return []

    return _parse_nbs_response(response.text, category, indicator, freq)


class NbsStatsSpider(Spider):
    name = "nbs_stats"
    start_urls: list[str] = []
    allowed_domains = {"data.stats.gov.cn", "www.stats.gov.cn"}
    concurrent_requests = 1
    download_delay = 2

    categories: list[str] = ["gdp", "price_indices", "industrial"]
    start_year: int = 2010

    def configure_sessions(self, manager):
        manager.add("default", FetcherSession(impersonate="chrome"), default=True)

    async def start_requests(self):
        for category in self.categories:
            cat_config = INDICATOR_CATEGORIES.get(category, {})
            for indicator, config in cat_config.items():
                freq = config["freq"]
                freq_suffix_map = {"hgjd": "A", "hgyd": "01", "hgnd": "", "fsjd": "A"}
                suffix = freq_suffix_map.get(config["dbcode"], "")
                start_period = f"{self.start_year}{suffix}" if suffix else str(self.start_year)
                url = _build_nbs_url(config["dbcode"], config["zbcode"], start_period)
                yield Request(
                    url=url,
                    callback=self.parse,
                    meta={
                        "category": category,
                        "indicator": indicator,
                        "freq": freq,
                    },
                )

    async def parse(self, response: Response):
        category = response.meta.get("category", "")
        indicator = response.meta.get("indicator", "")
        freq = response.meta.get("freq", "Q")

        if response.status == 200 and response.text:
            items = _parse_nbs_response(response.text, category, indicator, freq)
            for item in items:
                yield item
            logger.info("Yielded %d records for %s/%s", len(items), category, indicator)


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nbs_indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            category TEXT NOT NULL,
            indicator_type TEXT NOT NULL,
            value REAL,
            indicator_code TEXT,
            indicator_name TEXT,
            unit TEXT,
            frequency TEXT,
            source TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(period, category, indicator_type)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_period ON nbs_indicators(period)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_category ON nbs_indicators(category)
    """)
    conn.commit()
    return conn


def save_to_sqlite(items: list[dict[str, Any]], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO nbs_indicators
                (period, category, indicator_type, value, indicator_code,
                 indicator_name, unit, frequency, source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["period"],
                    item["category"],
                    item["indicator_type"],
                    item["value"],
                    item.get("indicator_code", ""),
                    item.get("indicator_name", ""),
                    item.get("unit", ""),
                    item.get("frequency", ""),
                    item.get("source", ""),
                    item.get("fetched_at", ""),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_to_json(items: list[dict[str, Any]], json_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


async def get_nbs_data(
    categories: list[str] | None = None,
    start_year: int = 2010,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch comprehensive data from National Bureau of Statistics.

    Args:
        categories: List of category keys. Available:
            - gdp: GDP data (quarterly, annual, by province)
            - price_indices: CPI/PPI monthly data
            - industrial: Industrial production and PMI
            - population: Annual population data
            - employment: Urban employment data
        start_year: Earliest year to include (default: 2010)

    Returns:
        Dict with category keys and list of indicator records.
    """
    if categories is None:
        categories = list(INDICATOR_CATEGORIES.keys())

    conn = init_db()
    results: dict[str, list[dict[str, Any]]] = {}

    logger.info("Starting NBS data fetch for categories: %s", ", ".join(categories))

    for category in categories:
        cat_config = INDICATOR_CATEGORIES.get(category, {})
        category_items: list[dict[str, Any]] = []

        for indicator in cat_config.keys():
            items = await _fetch_nbs_indicator(category, indicator, start_year)
            if items:
                category_items.extend(items)
                logger.info("  %s/%s: %d records", category, indicator, len(items))
            await asyncio.sleep(2)

        if category_items:
            results[category] = category_items
            n_sqlite = save_to_sqlite(category_items, conn)
            json_path = OUTPUT_DIR / f"nbs_{category}.json"
            save_to_json(category_items, json_path)
            logger.info(
                "Saved %d %s records: %d to SQLite, JSON to %s",
                len(category_items),
                category,
                n_sqlite,
                json_path,
            )

    conn.close()
    return results


def get_gdp_data(start_year: int = 2010) -> list[dict[str, Any]]:
    """Fetch GDP data from NBS."""
    results = asyncio.run(get_nbs_data(["gdp"], start_year))
    return results.get("gdp", [])


def get_price_indices(start_year: int = 2010) -> list[dict[str, Any]]:
    """Fetch CPI/PPI data from NBS."""
    results = asyncio.run(get_nbs_data(["price_indices"], start_year))
    return results.get("price_indices", [])


if __name__ == "__main__":
    results = asyncio.run(
        get_nbs_data(
            categories=["gdp", "price_indices", "industrial", "population"],
            start_year=2015,
        )
    )

    print(f"\n{'=' * 70}")
    print(f"NBS Data Fetch Complete")
    print(f"{'=' * 70}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON files in: {OUTPUT_DIR}")
    print(f"{'=' * 70}")

    for category, items in results.items():
        print(f"\n{category.upper()} ({len(items)} records):")
        if items:
            for item in sorted(items, key=lambda x: x.get("period", ""))[-5:]:
                print(
                    f"  {item['period']:>12s}  {item['value']:>12,.2f}  "
                    f"{item.get('unit', '')}  [{item.get('indicator_type', '')}]"
                )
