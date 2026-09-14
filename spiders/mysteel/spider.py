#!/usr/bin/env python3
"""MySteel (我的钢铁网) Scrapling Spider.

Two-step server-rendered flow (the old /price/ and /info/ list pages are dead):
1. DISCOVERY — fetch the homepage https://www.mysteel.com/ and collect the
   statically rendered anchors whose text contains 价格行情 and that point at
   article pages (https://<channel>.mysteel.com/m/....html).
2. DATA — fetch each article page (bounded) and parse the price rows from the
   server-rendered body: the JSON-LD application/ld+json description carries
   the price summary lines, and the #marketTable table carries per-row
   品名/规格/材质/钢厂 (its price cells are encrypted in static HTML, so the
   JSON-LD summary is the durable price source; table rows are merged with the
   summary when they match).
"""

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from scrapling import Fetcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

START_URLS: List[str] = ["https://www.mysteel.com/"]

CUSTOM_UAS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0",
]

MAX_RETRIES = 3
REQUEST_DELAY = 2.0

# Bounded run caps: at most 5 article pages followed per run, 60 data records.
MAX_ARTICLE_PAGES = 5
MAX_RECORDS = 60

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR_LOCAL = BASE_DIR / "output"
DATA_DB = DATA_DIR / "data.sqlite"
JSON_OUTPUT = OUTPUT_DIR_LOCAL / "export.jsonl"

ARTICLE_LINK_RE = re.compile(
    r'<a[^>]+href=["\']([^"\']*?mysteel\.com/m/[^"\'<>]+?\.html)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
LD_JSON_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")
DATE_TOKEN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PRICE_RE = re.compile(r"价格(\d[\d,]*(?:\.\d+)?元/吨(?:（[^）]*）)?)")
CHANGE_RE = re.compile(r"(环比[^，。<]+)")


def _strip_tags(fragment: str) -> str:
    return " ".join(TAG_RE.sub("", fragment).split())


def _iter_ld_json(html: str) -> List[dict]:
    """Return every parseable JSON-LD object found in the page."""
    objects: List[dict] = []
    for block in LD_JSON_RE.findall(html):
        try:
            data = json.loads(block)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            objects.append(data)
    return objects


def _ld_price_summary(html: str) -> Tuple[str, str, str]:
    """Find the JSON-LD WebPage entry that carries the price summary.

    Returns (name, description, url) of the first @graph entry (or top-level
    object) whose description contains a 元/吨 price line; empty strings if the
    page has no such entry.
    """
    for data in _iter_ld_json(html):
        candidates: List[dict] = []
        graph = data.get("@graph")
        if isinstance(graph, list):
            candidates.extend(item for item in graph if isinstance(item, dict))
        candidates.append(data)
        for item in candidates:
            name = item.get("name") or ""
            desc = item.get("description") or ""
            item_url = item.get("url") or ""
            if isinstance(name, str) and isinstance(desc, str) and "元/吨" in desc and "价格" in desc:
                return name, desc, item_url if isinstance(item_url, str) else ""
    return "", "", ""


def _parse_summary_lines(description: str) -> List[Dict[str, str]]:
    """Parse the JSON-LD description price lines into field dicts.

    Each line looks like:
      2026-09-11 高线 Φ6 HPB300 永钢 价格3670元/吨，环比上日下跌20元/吨，...
    """
    rows: List[Dict[str, str]] = []
    for raw_line in re.split(r"<br\s*/?>", description):
        line = " ".join(raw_line.split())
        if not line:
            continue
        price_m = PRICE_RE.search(line)
        if not price_m:
            continue
        tokens = [t for t in line.split(" ") if t and not DATE_TOKEN_RE.match(t)]
        price_idx = next((i for i, t in enumerate(tokens) if t.startswith("价格")), None)
        head = tokens[:price_idx] if price_idx is not None else tokens[:4]
        product, spec, material, mill = (head + ["", "", "", ""])[:4]
        change_m = CHANGE_RE.search(line)
        rows.append(
            {
                "product_name": product,
                "spec": spec,
                "material": material,
                "mill": mill,
                "price": price_m.group(1),
                "change": change_m.group(1) if change_m else "",
            }
        )
    return rows


def _parse_market_table(html: str) -> List[Dict[str, str]]:
    """Parse the #marketTable rows (品名/规格/材质/钢厂/价格/涨跌).

    Price/change cells that are encrypted in the static HTML (rendered as an
    icon-key link with no digits) are left empty.
    """
    table_start = html.find('id="marketTable"')
    if table_start < 0:
        return []
    table_end = html.find("</table>", table_start)
    table_html = html[table_start : table_end if table_end > 0 else len(html)]

    field_by_data_type = {
        "breed": "product_name",
        "spec": "spec",
        "material": "material",
        "place": "mill",
        "price": "price",
        "raise": "change",
    }
    positional_fields = ("product_name", "spec", "material", "mill")

    rows: List[Dict[str, str]] = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table_html, re.IGNORECASE | re.DOTALL):
        tds = re.findall(r"<td\b([^>]*)>(.*?)</td>", tr, re.IGNORECASE | re.DOTALL)
        if not tds:
            continue
        row: Dict[str, str] = {"product_name": "", "spec": "", "material": "", "mill": "", "price": "", "change": ""}
        data_cells: List[str] = []
        for attrs, content in tds:
            text = _strip_tags(content)
            data_cells.append(text)
            type_m = re.search(r'data-type=["\']([^"\']+)["\']', attrs)
            field = field_by_data_type.get(type_m.group(1) if type_m else "", "")
            if field and (field not in ("price", "change") or re.search(r"\d", text)):
                row[field] = text
        for field, text in zip(positional_fields, data_cells):
            if not row[field]:
                row[field] = text
        if any(row.values()):
            rows.append(row)
    return rows


def _fetch(url: str) -> Optional[Any]:
    """Fetch a URL with the existing retry/UA-rotation machinery."""
    for retry_count in range(MAX_RETRIES):
        try:
            fetcher = Fetcher(auto_match=False, impersonate="chrome")
            response = fetcher.get(
                url,
                timeout=30,
                stealthy_headers=True,
                headers={"User-Agent": CUSTOM_UAS[retry_count % len(CUSTOM_UAS)]},
            )
            if response.status == 200:
                return response
            logger.warning(
                "HTTP {} for {}, retry {}/{}".format(response.status, url, retry_count + 1, MAX_RETRIES)
            )
        except Exception as e:
            logger.error("Error fetching {}: {}".format(url, e))
        time.sleep(REQUEST_DELAY)
    logger.error("Failed to fetch {} after {} retries".format(url, MAX_RETRIES))
    return None


def parse_html(html: str, url: str = "") -> List[Dict[str, Any]]:
    """Parse a MySteel page (homepage OR article) into records.

    Dispatches on content:
    - article body (JSON-LD price summary or #marketTable): one record per
      price row (table rows merged with the JSON-LD summary; summary lines
      with no matching table row are emitted on their own);
    - otherwise, homepage: one record per 价格行情 article link.
    """
    scraped_at = datetime.now().isoformat()

    ld_name, ld_desc, ld_url = _ld_price_summary(html)
    has_table = 'id="marketTable"' in html

    if ld_desc or has_table:
        title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        article_title = ld_name or (_strip_tags(title_m.group(1)) if title_m else "")
        source_url = url or ld_url

        summary_rows = _parse_summary_lines(ld_desc)
        article_date = ""
        date_m = re.search(r"\d{4}-\d{2}-\d{2}", ld_desc)
        if date_m:
            article_date = date_m.group(0)

        table_rows = _parse_market_table(html)

        records: List[Dict[str, Any]] = []
        used_summary_indexes = set()

        # Merge table rows with summary lines on (product, spec, material, mill).
        for trow in table_rows:
            price = trow.get("price", "")
            change = trow.get("change", "")
            for idx, srow in enumerate(summary_rows):
                if idx in used_summary_indexes:
                    continue
                if all(
                    (trow.get(k, "") or "") == (srow.get(k, "") or "")
                    for k in ("product_name", "spec", "material", "mill")
                ):
                    price = price or srow.get("price", "")
                    change = change or srow.get("change", "")
                    used_summary_indexes.add(idx)
                    break
            if not price:
                continue  # encrypted price cell with no summary match: no data
            records.append(
                {
                    "product_name": trow.get("product_name", ""),
                    "spec": trow.get("spec", ""),
                    "material": trow.get("material", ""),
                    "mill": trow.get("mill", ""),
                    "price": price,
                    "change": change,
                    "date": article_date,
                    "article_title": article_title,
                    "record_kind": "price_row",
                    "source_url": source_url,
                    "scraped_at": scraped_at,
                }
            )

        # Summary lines beyond the table (or with no table at all).
        for idx, srow in enumerate(summary_rows):
            if idx in used_summary_indexes:
                continue
            records.append(
                {
                    "product_name": srow.get("product_name", ""),
                    "spec": srow.get("spec", ""),
                    "material": srow.get("material", ""),
                    "mill": srow.get("mill", ""),
                    "price": srow.get("price", ""),
                    "change": srow.get("change", ""),
                    "date": article_date,
                    "article_title": article_title,
                    "record_kind": "price_row",
                    "source_url": source_url,
                    "scraped_at": scraped_at,
                }
            )

        logger.info("Parsed {} price row(s) from {}".format(len(records), url or "html body"))
        return records[:MAX_RECORDS]

    # Discovery: homepage anchors whose text contains 价格行情.
    discovery: List[Dict[str, Any]] = []
    seen_urls = set()
    for href, anchor in ARTICLE_LINK_RE.findall(html):
        text = _strip_tags(anchor)
        if "价格行情" not in text:
            continue
        article_url = urljoin(url, href.strip()) if url else href.strip()
        if not article_url or article_url in seen_urls:
            continue
        seen_urls.add(article_url)
        discovery.append(
            {
                "article_title": text,
                "article_url": article_url,
                "record_kind": "listing_link",
                "source_url": url,
                "scraped_at": scraped_at,
            }
        )

    logger.info("Parsed {} listing link(s) from {}".format(len(discovery), url or "html body"))
    return discovery


def parse_response(response: Any) -> List[Dict[str, Any]]:
    """Parse a fetched response (single source of truth: parse_html)."""
    return parse_html(response.text, url=response.url)


def save_to_sqlite(records: List[Dict[str, Any]]) -> None:
    """Save scraped records to SQLite database."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DATA_DB)
    cursor = conn.cursor()

    if not records:
        logger.warning("No records to save to SQLite")
        conn.close()
        return

    sample = records[0]
    columns = []
    insert_params = []
    placeholders = []

    for key, value in sample.items():
        col_name = key.lower().replace(" ", "_").replace("-", "_")
        if isinstance(value, str):
            col_type = "TEXT"
        elif isinstance(value, (int, float)):
            col_type = "REAL"
        else:
            col_type = "TEXT"

        columns.append((col_name, col_type))
        insert_params.append(col_name)
        placeholders.append("?")

    table_name = "mysteel_records"
    col_defs = ", ".join([c[0] + " " + c[1] for c in columns])
    sql_create = """CREATE TABLE IF NOT EXISTS """ + table_name + """ (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        """ + ", ".join([c[0] for c in columns]) + """
    )"""
    cursor.execute(sql_create)

    placeholders_str = ", ".join(placeholders)
    insert_sql = "INSERT INTO " + table_name + " (" + ", ".join(insert_params) + ") VALUES (" + placeholders_str + ")"

    for record in records:
        values = [record.get(col[0], None) for col in columns]
        cursor.execute(insert_sql, values)

    conn.commit()
    conn.close()
    logger.info("Saved {} records to {}".format(len(records), DATA_DB))


def save_to_json(records: List[Dict[str, Any]]) -> None:
    """Save scraped records to JSON Lines file."""
    OUTPUT_DIR_LOCAL.mkdir(parents=True, exist_ok=True)

    if not records:
        logger.warning("No records to save to JSON")
        return

    with open(JSON_OUTPUT, "a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    logger.info("Saved {} records to {}".format(len(records), JSON_OUTPUT))


def run_spider(urls: Optional[List[str]] = None, save_results: bool = True) -> List[Dict[str, Any]]:
    """Main entry point for running the spider (two-step homepage→article flow)."""
    urls = urls or START_URLS
    all_records: List[Dict[str, Any]] = []

    logger.info("Starting spider for {} URL(s)...".format(len(urls)))
    logger.info("Target: {}".format(urls[0]))

    pages_fetched = 0
    page_cap = 1 + MAX_ARTICLE_PAGES  # entry page + followed article pages

    for url in urls:
        if pages_fetched >= page_cap or len(all_records) >= MAX_RECORDS:
            break
        response = _fetch(url)
        time.sleep(REQUEST_DELAY)
        if response is None:
            continue
        pages_fetched += 1
        records = parse_response(response)
        all_records.extend(r for r in records if r.get("record_kind") != "listing_link")

        # Step 2: follow discovered article links (deduped, bounded).
        article_urls = [r["article_url"] for r in records if r.get("record_kind") == "listing_link"]
        for article_url in dict.fromkeys(article_urls):
            if pages_fetched >= page_cap or len(all_records) >= MAX_RECORDS:
                break
            article_response = _fetch(article_url)
            time.sleep(REQUEST_DELAY)
            if article_response is None:
                continue
            pages_fetched += 1
            all_records.extend(parse_response(article_response))

    all_records = all_records[:MAX_RECORDS]

    if save_results:
        save_to_sqlite(all_records)
        save_to_json(all_records)

    logger.info("Spider completed. Total records: {}".format(len(all_records)))
    return all_records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrapling spider for MySteel (我的钢铁网)")
    parser.add_argument("--urls", nargs="+", help="URLs to crawl (default: START_URLS)")
    parser.add_argument("--dry-run", action="store_true", help="Run without saving results")
    args = parser.parse_args()

    urls = args.urls if args.urls else START_URLS
    save = not args.dry_run

    run_spider(urls=urls, save_results=save)
