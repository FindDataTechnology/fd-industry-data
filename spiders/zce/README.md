# ZCE Spider - Zhengzhou Commodity Exchange Scraper

## Overview

Scrapyling-based spider for extracting **Zhengzhou Commodity Exchange (郑州商品交易所)** agricultural and chemical futures prices. Scrapes data from [https://www.czce.com.cn](https://www.czce.com.cn).

**Score: 98/100** - High-priority official exchange source

---

## What This Extracts

### 🌾 Agricultural Products
- **棉花 (Cotton)** - Cotton futures
- **白糖 (Sugar)** - Sugar futures
- **PTA** - Purified terephthalic acid futures
- **甲醇 (Methanol)** - Methanol futures
- **PP (聚丙烯)** - Polypropylene futures
- **苹果 (Apple)** - Apple futures
- **红枣 (Jujube dates)** - Jujube futures

### ⚗️ Chemical Products
- **短纤 (Short fiber)** - Short fiber futures
- **聚酯链 (Polyester chain)** - Polyester product futures
- **CA (共聚甲醛)** - Copolymer futures

### ⚡ Coal & Coke
- **动力煤 (Thermal coal)** - Thermal coal futures
- **焦炭 (Coke)** - Coke futures
- **甲醇 (Methanol)** - Methanol futures

### 📊 Market Overview
- Market statistics and trading volumes
- Economic indicators
- Industry reports and news

---

## Quick Start

### 1. Install Dependencies

```bash
cd /Users/chengsishi/finddata/fd-industry-data/spiders/zce
pip install "scrapling[all]>=0.4.7"
scrapling install --force
```

### 2. Run the Spider

```bash
python spider.py
```

Or with asyncio directly:

```bash
python -c "from spider import run_spider; import asyncio; asyncio.run(run_spider())"
```

---

## Data Output

### SQLite Database
**Location:** `data/zce_prices.db`

Tables created automatically:
- `agricultural_spot` - Agricultural spot prices
- `agricultural_futures` - Agricultural futures pricing
- `chemicals_spot` - Chemical product spot prices
- `chemicals_futures` - Chemical product futures pricing
- `coal_coke_spot` - Coal and coke spot prices
- `coal_coke_futures` - Coal and coke futures pricing
- `market_overview` - Market statistics
- `economic_news` - Economic news articles

Each table contains:
- Contract name/ticker
- Opening, high, low, close prices
- Settlement price
- Price change and percentage
- Trading volume
- Open interest
- Timestamp of scrape

### JSON Export
**Location:** `output/*.json`

Separate files by category and price type:
- `agricultural.json`
- `agricultural_spot.json`
- `agricultural_futures.json`
- `chemicals.json`
- `coal_coke.json`
- `market_overview.json`
- `economic_news.json`

---

## Rate Limiting & Anti-Bot Protection

The spider implements comprehensive anti-bot measures:

- ✅ **User-Agent rotation** - Chrome impersonation
- ✅ **Rate limiting** - 3-second delays between requests
- ✅ **Max retries** - 3 retry attempts per request
- ✅ **Request throttling** - Max 2 concurrent requests
- ✅ **Robots.txt compliance** - Follows robots.txt rules
- ✅ **Timeout protection** - 30-second timeout per request

**Why these settings?**  
ZCE's website has moderate bot detection. The conservative approach ensures reliable extraction without triggering anti-scraping mechanisms.

---

## Database Schema

```sql
CREATE TABLE {category}_{price_type} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at TEXT,        -- ISO timestamp of scrape
    source_url TEXT,        -- Original data source URL
    category TEXT,          -- Data category
    price_type TEXT,        -- spot/futures/regular
    contract_name TEXT,     -- Contract/product name
    opening REAL,           -- Opening price
    high REAL,              -- Highest price
    low REAL,               -- Lowest price
    close REAL,             -- Closing price
    settlement REAL,        -- Settlement price
    change TEXT,            -- Price change
    volume REAL,            -- Trading volume
    open_interest REAL,     -- Open interest
    other_data JSONB        -- Flexible additional fields
);
```

---

## Configuration

Edit `spider.py` to customize start URLs:

```python
START_URLS = [
    "https://www.czce.com.cn/zh/cn/ssps/index.html",  # Spot prices
    "https://www.czce.com.cn/zh/cn/yhqh/index.html",  # Futures info
    "https://www.czce.com.cn/zh/cn/hsqk/index.html",  # Market overview
    "https://www.czce.com.cn/zh/cn/zcjj/index.html",  # Economic indicators
]
```

Category-specific URLs:
```python
CATEGORY_URLS = {
    "agricultural": ["..."],
    "chemicals": ["..."],
    "coal_coke": ["..."],
}
```

Concurrent requests and delay settings:
```python
concurrent_requests = 2    # Conservative for ZCE
download_delay = 3.0       # Seconds between requests
max_retries = 3            # Retry failures
timeout = 30              # Request timeout (seconds)
```

---

## Authentication Requirements

**No authentication required.** All market data is publicly available on ZCE's website.

---

## Known Issues

1. **Complex navigation**: ZCE uses complex URL patterns with Chinese characters
2. **Dynamic pricing**: Real-time prices require WebSocket or API access
3. **Contract rollover**: Futures contracts expire and are replaced - track active vs expired
4. **Chinese units**: Some volumes/prices use Chinese units (万，亿) - handled automatically
5. **Pagination**: Some pages use JavaScript pagination - may require Playwright for full coverage

---

## License

MIT License - See parent directory LICENSE file

---

## Updates

This spider should be updated periodically as ZCE changes their website structure. Monitor for:
- Category URL structure changes
- Table format updates
- New product listings
- Pagination system modifications
