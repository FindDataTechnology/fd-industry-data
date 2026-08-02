# DCE Spider - Dalian Commodity Exchange Scraper

## Overview

Scrapyling-based spider for extracting **Dalian Commodity Exchange (大连商品交易所)** agricultural and chemical futures prices. Scrapes data from [https://www.dce.com.cn](https://www.dce.com.cn).

**Score: 98/100** - High-priority official exchange source

---

## What This Extracts

### 🌾 Agricultural Products
- **大豆 A (Soybeans A)** - Soybean futures contracts
- **豆粕 (Soybean Meal)** - Feed ingredient futures
- **玉米 (Corn)** - Corn futures
- **棕榈油 (Palm Oil)** - Palm oil futures

### ⚗️ Chemical Products
- **PP (聚丙烯)** - Polypropylene futures
- **PE (聚乙烯)** - Polyethylene futures
- **PVC (聚氯乙烯)** - PVC resin futures
- **苯乙烯 (Styrene)** - Styrene monomer futures

### 🐖 Livestock
- **生猪 (Live Hogs)** - Live hog futures contract

### 📈 Market Analysis
- Economic analysis reports
- Market news and commentary
- Industry insights

---

## Quick Start

### 1. Install Dependencies

```bash
cd /Users/chengsishi/finddata/fd-industry-data/spiders/dce
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
**Location:** `data/dce_prices.db`

Tables created automatically:
- `agriculture` - Agricultural product pricing
- `chemicals` - Chemical product pricing  
- `livestock` - Livestock futures pricing
- `general` - General futures data
- `analysis` - Market analysis articles

Each table contains:
- Contract name/ticker
- Opening, high, low, close prices
- Settlement price
- Price change and percentage
- Trading volume
- Open interest (持仓量)
- Timestamp of scrape

### JSON Export
**Location:** `output/*.json`

Separate files by category:
- `agriculture.json`
- `chemicals.json`
- `livestock.json`
- `analysis.json`

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
DCE's website structure requires careful navigation through category pages. The conservative approach ensures reliable extraction without triggering anti-scraping mechanisms.

---

## Database Schema

```sql
CREATE TABLE {category} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at TEXT,        -- ISO timestamp of scrape
    source_url TEXT,        -- Original data source URL
    category TEXT,          -- Data category
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
    "https://www.dce.com.cn/dalianshangpin/flk/index.html",
    "https://www.dce.com.cn/dalianshangpin/qhsj/index.html",
    "https://www.dce.com.cn/dalianshangpin/jcyj/index.html",
]
```

Category-specific URLs:
```python
CATEGORY_URLS = {
    "agriculture": ["..."],
    "chemicals": ["..."],
    "livestock": ["..."],
}
```

Concurrent requests and delay settings:
```python
concurrent_requests = 2    # Conservative for DCE
download_delay = 3.0       # Seconds between requests
max_retries = 3            # Retry failures
timeout = 30              # Request timeout (seconds)
```

---

## Authentication Requirements

**No authentication required.** All market data is publicly available on DCE's website.

---

## Known Issues

1. **Complex navigation**: DCE uses complex URL patterns with Chinese characters
2. **Dynamic pricing**: Real-time prices require WebSocket or API access
3. **Contract rollover**: Futures contracts expire and are replaced - track active vs expired
4. **Chinese units**: Some volumes/prices use Chinese units (万，亿) - handled automatically

---

## License

MIT License - See parent directory LICENSE file

---

## Updates

This spider should be updated periodically as DCE changes their website structure. Monitor for:
- Category URL structure changes
- Table format updates
- New product listings
- Pagination system modifications
