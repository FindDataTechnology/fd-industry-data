# CISA Spider - China Iron & Steel Association Scraper

## Overview

Scrapyling-based spider for extracting **China Iron & Steel Association (中国钢铁工业协会)** industry data. Scrapes data from [https://www.cisa.org.cn](https://www.cisa.org.cn).

**Score: 98/100** - High-priority official industry association source

---

## What This Extracts

### 🏭 Production Statistics
- **Production Output** - Monthly/quarterly/annual steel production volumes
- **Production Efficiency** - Capacity utilization rates and efficiency metrics
- **Capacity Utilization** - Steel mill capacity and utilization data

### 📊 Trade Data
- **Import Statistics** - Steel product import volumes and values
- **Export Statistics** - Steel product export volumes and values
- **Trade Balance** - Net trade position by product category

### 💰 Price Indices
- **Price Indices** - Official CISA steel price indices
- **Regional Prices** - Regional steel pricing data
- **Product Variety Prices** - Prices by steel product type (rebar, plate, coil, etc.)

### 📈 Industry Analysis
- **Analysis Reports** - In-depth industry analysis reports
- **Market Analysis** - Current market conditions and trends
- **Market Outlook** - Future market projections and forecasts

---

## Quick Start

### 1. Install Dependencies

```bash
cd /Users/chengsishi/finddata/fd-industry-data/spiders/cisa
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
**Location:** `data/cisa_data.db`

Tables created automatically:
- `production` - Production statistics and output data
- `trade` - Import/export trade data
- `price` - Price indices and regional pricing
- `analysis` - Industry analysis reports and articles
- `general` - General steel industry data

Each table contains:
- Title/heading
- Publication date
- Content/data
- Source URL
- Timestamp of scrape

### JSON Export
**Location:** `output/*.json`

Separate files by category:
- `production.json`
- `trade.json`
- `price.json`
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
CISA's website has moderate bot detection. The conservative approach ensures reliable extraction without triggering anti-scraping mechanisms.

---

## Database Schema

```sql
CREATE TABLE {category} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at TEXT,        -- ISO timestamp of scrape
    source_url TEXT,        -- Original data source URL
    category TEXT,          -- Data category
    title TEXT,             -- Article/report title
    url TEXT,               -- Article URL
    publish_date TEXT,      -- Publication date
    content TEXT,           -- Article content (truncated)
    other_data JSONB        -- Flexible additional fields
);
```

---

## Configuration

Edit `spider.py` to customize start URLs:

```python
START_URLS = [
    "https://www.cisa.org.cn/",  # Homepage
    "https://www.cisa.org.cn/production/",  # Production statistics
    "https://www.cisa.org.cn/trade/",  # Import/export data
    "https://www.cisa.org.cn/price/",  # Price indices
    "https://www.cisa.org.cn/analysis/",  # Industry analysis
]
```

Category-specific URLs:
```python
CATEGORY_URLS = {
    "production": ["..."],
    "trade": ["..."],
    "price": ["..."],
    "analysis": ["..."],
}
```

Concurrent requests and delay settings:
```python
concurrent_requests = 2    # Conservative for CISA
download_delay = 3.0       # Seconds between requests
max_retries = 3            # Retry failures
timeout = 30              # Request timeout (seconds)
```

---

## Authentication Requirements

**No authentication required.** All industry data is publicly available on CISA's website.

---

## Known Issues

1. **Complex navigation**: CISA uses complex URL patterns with Chinese characters
2. **Dynamic content**: Some pages may require JavaScript execution for full data
3. **Pagination**: Some sections use JavaScript pagination - may require Playwright for full coverage
4. **Chinese units**: Some volumes/prices use Chinese units (万，亿) - handled automatically
5. **Report access**: Some detailed reports may require member login - not scraped

---

## License

MIT License - See parent directory LICENSE file

---

## Updates

This spider should be updated periodically as CISA changes their website structure. Monitor for:
- Category URL structure changes
- Table format updates
- New data sections
- Pagination system modifications
