#!/usr/bin/env python3
"""Generate steel industry spiders for fd-industry-data project."""

import json
from pathlib import Path

# Steel data sources organized by category
STEEL_SOURCES = {
    "steel-assoc": {
        "name": "Steel Industry Associations",
        "urls": [
            {"url": "https://www.cisa.org.cn", "title": "中国钢铁工业协会", "description": "中国钢铁行业权威组织，发布官方产量、进出口、价格指数及行业运行报告"},
            {"url": "https://www.worldsteel.org", "title": "World Steel Association", "description": "全球钢铁协会，提供世界范围内粗钢产量、需求预测及环保数据"},
            {"url": "http://www.stats.gov.cn/", "title": "国家统计局 - 工业数据", "description": "政府官方发布的黑色金属冶炼及压延加工业月度与年度统计数据"},
            {"url": "https://www.steelhome.cn", "title": "钢之家", "description": "专业钢铁行业市场资讯，包含价格监测、库存数据及行业研究报告"},
            {"url": "https://www.shfe.com.cn", "title": "上海期货交易所", "description": "螺纹钢、热卷等钢铁期货合约交易数据及持仓信息"},
        ],
        "tables": [
            {"name": "association_data", "columns": [{"name": "report_date", "type": "TEXT", "desc": "报告期"}, {"name": "indicator_name", "type": "TEXT", "desc": "指标名称"}, {"name": "value", "type": "REAL", "desc": "数值"}, {"name": "unit", "type": "TEXT", "desc": "单位"}, {"name": "source_url", "type": "TEXT", "desc": "来源网址"}]}
        ]
    },
    "steel-exchange": {
        "name": "Steel Exchanges & Data Platforms",
        "urls": [
            {"url": "https://www.shfe.com.cn/", "title": "上海期货交易所", "description": "官方期货市场数据，包含螺纹钢（RB）、线材等钢材期货合约的交易、持仓及结算信息"},
            {"url": "https://www.mysteel.com/", "title": "我的钢铁网", "description": "中国领先的钢铁行业大数据平台，提供钢材价格、库存、产能及供需分析数据"},
            {"url": "https://www.wind.com.cn/", "title": "万得金融终端", "description": "专业金融数据服务，涵盖钢铁行业现货与期货市场行情及宏观经济关联数据"},
            {"url": "https://www.smm.cn/", "title": "上海有色网", "description": "有色金属及黑色金属产业链数据，包含钢铁原材料及成品市场价格行情"},
            {"url": "http://www.steelhome.cn/", "title": "钢之家", "description": "钢铁行业资讯与数据统计网站，提供钢厂排产、进出口及市场走势分析"},
            {"url": "http://data.stats.gov.cn/", "title": "国家统计数据库", "description": "国家统计局官方发布，包含粗钢产量、钢材进出口量等宏观统计数据"},
        ],
        "tables": [
            {"name": "exchange_data", "columns": [{"name": "contract_code", "type": "TEXT", "desc": "合约代码"}, {"name": "trade_date", "type": "TEXT", "desc": "交易日"}, {"name": "open_price", "type": "REAL", "desc": "开盘价"}, {"name": "high_price", "type": "REAL", "desc": "最高价"}, {"name": "low_price", "type": "REAL", "desc": "最低价"}, {"name": "close_price", "type": "REAL", "desc": "收盘价"}, {"name": "volume", "type": "INTEGER", "desc": "成交量"}, {"name": "turnover", "type": "REAL", "desc": "成交额"}, {"name": "open_interest", "type": "REAL", "desc": "持仓量"}, {"name": "source_url", "type": "TEXT", "desc": "来源网址"}]}
        ]
    },
    "steel-statistics": {
        "name": "Steel Statistical Office Websites",
        "urls": [
            {"url": "https://www.cisa.org.cn/", "title": "中国钢铁工业协会", "description": "提供中国钢铁行业月度产量、产能利用率、进出口及市场价格统计数据。"},
            {"url": "http://www.stats.gov.cn/", "title": "国家统计局", "description": "官方工业统计数据库，包含黑色金属冶炼和压延加工业的产值、产量等宏观数据。"},
            {"url": "http://www.miit.gov.cn/", "title": "工业和信息化部", "description": "发布钢铁行业运行情况及产业政策信息，包含重点监测企业数据。"},
            {"url": "http://www.worldsteel.org/zh-hans/", "title": "世界钢铁协会", "description": "全球钢铁生产与需求统计数据，包含各主要产钢国月度数据。"},
        ],
        "tables": [
            {"name": "statistical_data", "columns": [{"name": "stat_date", "type": "TEXT", "desc": "统计日期"}, {"name": "region", "type": "TEXT", "desc": "地区"}, {"name": "indicator_type", "type": "TEXT", "desc": "指标类型"}, {"name": "current_value", "type": "REAL", "desc": "本期值"}, {"name": "prev_value", "type": "REAL", "desc": "上期值"}, {"name": "yoy_change", "type": "REAL", "desc": "同比变化"}, {"name": "mom_change", "type": "REAL", "desc": "环比变化"}, {"name": "unit", "type": "TEXT", "desc": "单位"}, {"name": "source_url", "type": "TEXT", "desc": "来源网址"}]}
        ]
    },
    "steel-info": {
        "name": "Steel Industry Information Networks",
        "urls": [
            {"url": "https://www.mysteel.com", "title": "MySteel (我的钢铁网)", "description": "Leading China steel market platform offering daily prices, production stats, and analysis."},
            {"url": "http://www.cisa.org.cn", "title": "China Iron and Steel Association", "description": "Official association providing industry statistics, policies, and regulatory reports."},
            {"url": "http://www.steelhome.cn", "title": "SteelHome (钢之家)", "description": "Comprehensive steel market information including price trends and inventory levels."},
            {"url": "https://www.smm.cn", "title": "SMM (上海有色网)", "description": "Major metals information portal covering ferrous and non-ferrous metal trading data."},
            {"url": "https://www.stats.gov.cn", "title": "National Bureau of Statistics of China", "description": "Official government source for industrial output and production statistics."},
        ],
        "tables": [
            {"name": "info_data", "columns": [{"name": "publish_date", "type": "TEXT", "desc": "发布日期"}, {"name": "category", "type": "TEXT", "desc": "分类"}, {"name": "title", "type": "TEXT", "desc": "标题"}, {"name": "content_summary", "type": "TEXT", "desc": "内容摘要"}, {"name": "metrics", "type": "TEXT", "desc": "相关指标 (JSON)"}, {"name": "source_url", "type": "TEXT", "desc": "来源网址"}]}
        ]
    },
    "steel-market": {
        "name": "Steel Market Data Platforms",
        "urls": [
            {"url": "https://www.mysteel.com/", "title": "MySteel (我的钢铁网)", "description": "Leading Chinese platform providing real-time steel prices, inventory levels, production data, and industry analysis."},
            {"url": "https://www.worldsteel.org/", "title": "World Steel Association", "description": "Global organization offering statistical data on steel production, consumption, and trade across countries."},
            {"url": "https://www.crugroup.com/", "title": "CRU Group", "description": "Provides independent steel market intelligence, price assessments, and forecasting services globally."},
            {"url": "https://www.fastmarkets.com/", "title": "Fastmarkets", "description": "Offers ferrous metal price assessments, news, and analytics for the steel industry."},
            {"url": "http://www.stats.gov.cn/", "title": "National Bureau of Statistics of China", "description": "Official government source for verified steel production volumes and import/export statistics."},
        ],
        "tables": [
            {"name": "market_data", "columns": [{"name": "quote_date", "type": "TEXT", "desc": "报价日期"}, {"name": "product_type", "type": "TEXT", "desc": "产品类型"}, {"name": "grade_spec", "type": "TEXT", "desc": "规格型号"}, {"name": "price", "type": "REAL", "desc": "价格"}, {"name": "price_unit", "type": "TEXT", "desc": "价格单位"}, {"name": "market_location", "type": "TEXT", "desc": "市场地点"}, {"name": "trend_direction", "type": "TEXT", "desc": "趋势方向"}, {"name": "source_url", "type": "TEXT", "desc": "来源网址"}]}
        ]
    }
}

def generate_spider_py(category: str, config: dict) -> str:
    """Generate spider.py content for a category."""
    
    table_name = config["tables"][0]["name"] if config["tables"] else f"{category}_data"
    urls_str = ",\n        ".join([f'"{url["url"]}"' for url in config["urls"]])
    
    return f'''#!/usr/bin/env python3
"""
{config['name']} Spider — Extracting data from {len(config['urls'])} steel industry sources.

Uses Scrapling's Spider framework with:
  - Multiple URLs per category
  - Automatic header rotation to avoid blocking
  - Error handling and retry logic
  - SQLite + JSON output

Output Structure:
  - data/{table_name}.db (SQLite database)
  - output/{table_name}.json (JSON export)

Categories: {category.replace('-', ', ').title()}
Total Sources: {len(config['urls'])}
"""

from __future__ import annotations

import json
import logging
import random
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
logger = logging.getLogger("{category}")

# ===== Configuration =====
BASE_DIR = Path(__file__).resolve().parent.parent.parent / "{category}"
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "{table_name}.db"
JSON_PATH = OUTPUT_DIR / "{table_name}.json"

# Target URLs for this category
START_URLS = [
        {urls_str}
    ]

# Custom User Agents to rotate through
CUSTOM_UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/128.0.0.0 Safari/537.36",
]

MAX_RETRIES = 3
REQUEST_DELAY = 2.0

# Source metadata mapping
SOURCE_INFO = {{
    {", ".join([f'"{url["url"]}": "{{"title": "{url["title"]}", "description": "{url["description"]}"}}' for url in config["urls"])]}
}}


def get_random_ua() -> str:
    """Return a random user agent to avoid detection."""
    return random.choice(CUSTOM_UAS)


def extract_data_from_page(html: str, page_url: str) -> list[dict[str, Any]]:
    """Extract relevant data from scraped HTML page.
    
    This is where you'd implement parsing logic specific to each site.
    The current implementation provides placeholder structure.
    """
    items = []
    
    # Placeholder: In real implementation, parse html using BeautifulSoup or similar
    # Example extraction patterns would be site-specific
    
    source_info = SOURCE_INFO.get(page_url, {{"title": "", "description": ""}})
    
    # Add timestamp
    fetched_at = datetime.now(timezone.utc).isoformat()
    
    logger.info("Extracted data from %s (source: %s)", page_url, source_info.get('title', 'Unknown'))
    
    return items  # Return empty for now until scraping logic is implemented


class {category.title().replace('-', '').replace('_', '')}Spider(Spider):
    """Spider for {config['name']}.
    
    Crawls multiple steel industry data sources within this category.
    Each URL may require custom parsing logic based on its structure.
    """
    
    name = "{category}"
    allowed_domains = set(u.split("//")[-1].split("/")[0] for u in START_URLS)
    start_urls = START_URLS
    
    # Rate limiting settings
    download_delay = REQUEST_DELAY
    concurrent_requests = 2
    
    # Retry configuration
    max_retries = MAX_RETRIES
    retry_statuses = [{{429, 500, 502, 503, 504}}]
    
    def configure_sessions(self, manager):
        """Configure FetcherSessions with custom headers."""
        for _ in range(self.concurrent_requests):
            session = FetcherSession(impersonate="chrome")
            session.update_headers({{
                "User-Agent": get_random_ua(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }})
            manager.add("default", session, default=True)
    
    async def parse(self, response: Response):
        """Parse each crawled page and extract data items."""
        
        if response.status != 200:
            logger.warning("Failed to fetch %s (status=%d)", response.url, response.status)
            return
        
        try:
            extracted_items = extract_data_from_page(response.text, response.url)
            
            for item in extracted_items:
                item["_source_url"] = response.url
                yield item
            
            logger.info("Page %s yielded %d items", response.url, len(extracted_items))
            
        except Exception as e:
            logger.error("Error parsing %s: %s", response.url, e)
            return


def save_to_sqlite(items: list[dict[str, Any]], db_path: Path = DB_PATH) -> int:
    """Save items to SQLite database with upsert support."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    
    # Create table based on schema definition
    columns_config = {column["name"]: column["type"] for column in {column["name"]: column for column in ''' + str([col["name"] + ":" + col["type"] for col in config["tables"][0]["columns"]]).replace("'", '"') + '''}.values()}
    columns_def = ", ".join([f"{col['name']} {col['type']}" for col in {column["name"]: column for column in ''' + str([{"name": col["name"], "type": col["type"], "desc": col["desc"]} for col in config["tables"][0]["columns"]]).replace("'", '"') + '''].values()])
    placeholders = ", ".join(["?" for _ in {column["name"]: column for column in ''' + str([{"name": col["name"], "type": col["type"], "desc": col["desc"]} for col in config["tables"][0]["columns"]]).replace("'", '"') + '''].keys())], "?")
    unique_keys = ", ".join([list({column["name"]: column for column in ''' + str([col["name"] for col in config["tables"][0]["columns"]]).replace("'", '"') + '''}.keys())[0]])
    
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {{{table_name}}} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {{{columns_def}}},
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    inserted = 0
    for item in items:
        try:
            values = [item.get(col, None) for col in {column["name"]: column for column in ''' + str([{"name": col["name"], "type": col["type"], "desc": col["desc"]} for col in config["tables"][0]["columns"]]).replace("'", '"') + '''].keys()]
            cur.execute(f"""
                INSERT OR REPLACE INTO {{{table_name}}} ({""", """.join({column["name"]: column for column in ''' + str([col["name"] for col in config["tables"][0]["columns"]]).replace("'", '"') + '''}.keys())), fetched_at
                VALUES ({placeholders})
            """, values + [datetime.now(timezone.utc).isoformat()])
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
            continue
    
    conn.commit()
    conn.close()
    return inserted


def save_to_json(items: list[dict[str, Any]], json_path: Path = JSON_PATH) -> None:
    """Save items to JSON file."""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def run_spider(urls: list[str] | None = None, save_results: bool = True) -> list[dict[str, Any]]:
    """Run the spider and optionally save results.
    
    Args:
        urls: Optional list of specific URLs to crawl. If None, uses all defined URLs.
        save_results: Whether to save results to SQLite and JSON files.
    
    Returns:
        List of extracted items.
    """
    spider = {category.title().replace('-', '').replace('_', '')}Spider()
    if urls:
        spider.start_urls = urls
    
    result = spider.start()
    items = list(result.items)
    
    if save_results and items:
        n_db = save_to_sqlite(items)
        save_to_json(items)
        logger.info("Saved %d records: %d to SQLite (%s), JSON (%s)", 
                   len(items), n_db, DB_PATH, JSON_PATH)
    elif not items:
        logger.warning("No items extracted from any source")
    
    return items


if __name__ == "__main__":
    logger.info("=" * 70)
    logger.info("Starting %s crawler", "%s".lower())
    logger.info("Total URLs to crawl: %d", len(START_URLS))
    for i, url in enumerate(START_URLS, 1):
        info = SOURCE_INFO.get(url, {{}})
        title = info.get("title", "Unknown")
        logger.info("  [%d] %s → %s", i, url[:50], title[:40] if title else "")
    logger.info("=" * 70)
    
    results = run_spider(save_results=True)
    
    print("\\n" + "=" * 70)
    print(f"Extraction Summary")
    print(f"{'=' * 70}")
    print(f"  Total records extracted: {len(results)}")
    print(f"  Database: {DB_PATH}")
    print(f"  JSON:      {JSON_PATH}")
    print(f"{'=' * 70}")
    
    if results:
        print(f"\\nSample records (first 3):")
        for r in results[:3]:
            print(f"  {json.dumps(r, ensure_ascii=False)[:100]}...")
