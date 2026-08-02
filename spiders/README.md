# Industry Association & Commodity Exchange Scrapers

Specialized Scrapyling scrapers for extracting structured data from Chinese industry associations and commodity exchanges.

## Overview

This collection provides 5 specialized web scrapers for extracting industry statistics, financial data, and commodity prices from authoritative Chinese sources.

### Target Sources

| Source | URL | Score | Category | Data Type |
|--------|-----|-------|----------|-----------|
| **China Iron & Steel Association** | https://www.cisa.org.cn | 98 | Industry Production | Steel production stats, capacity utilization |
| **AMAC (Asset Management Association)** | https://www.amac.org.cn | 98 | Fund Registration | AUM statistics, fund manager data |
| **Securities Association of China** | https://www.sac.net.cn | 95 | Securities Trading | Trading volume, market data |
| **CME Group Agriculture** | https://www.cmegroup.com/markets/agricultural.html | 90 | Agricultural Futures | Futures prices, open interest |
| **Shanghai Futures Exchange** | https://www.shfe.com.cn | 92 | Metal Futures | Settlement prices, inventory data |

## Installation

```bash
cd /Users/chengsishi/finddata/fd-industry-data

# Activate virtual environment
source .venv/bin/activate

# Install dependencies (if not already installed)
uv sync
```

## Usage

### 1. China Iron & Steel Association (CISA) Spider

Extracts steel industry production statistics and capacity utilization data.

```bash
# Run the spider
uv run python spiers/cisa_spider.py

# Output files:
# - spiers/cisa_spider/data/cisa_production.db
# - spiers/cisa_spider/output/production_statistics.json
```

**Data Extracted:**
- Production statistics (monthly/quarterly)
- Capacity utilization rates
- Import/export volumes
- Price indices
- Market analysis reports

### 2. AMAC (Asset Management Association) Spider

Extracts fund manager registration and AUM statistics.

```bash
# Run the spider
uv run python spiers/mac_spider.py

# Output files:
# - spiers/mac_spider/data/amac_fund_data.db
# - spiers/mac_spider/output/fund_statistics.json
```

**Data Extracted:**
- Fund manager registration counts
- Assets Under Management (AUM) totals
- Private equity fund data
- Product registration statistics
- Industry growth metrics

### 3. Securities Association of China (SAC) Spider

Extracts securities trading volume and market statistics.

```bash
# Run the spider
uv run python spiers/sac_spider.py

# Output files:
# - spiers/sac_spider/data/sac_securities_data.db
# - spiers/sac_spider/output/securities_trading.json
```

**Data Extracted:**
- Trading volume by exchange
- Broker/securities company counts
- Market index performance
- IPO transaction data
- Investment banking statistics

### 4. CME Group Agriculture Futures Spider

Extracts agricultural commodity futures prices and trading data.

```bash
# Run the spider
uv run python spiers/cmegroup_ag_spider.py

# Output files:
# - spiers/cmegroup_ag_spider/data/cme_ag_futures.db
# - spiers/cmegroup_ag_spider/output/agricultural_futures.json
```

**Data Extracted:**
- Settlement prices (corn, wheat, soybeans)
- Open interest by contract month
- Trading volume statistics
- Historical price data
- Contract specifications

### 5. Shanghai Futures Exchange (SHFE) Spider

Extracts metal futures settlement prices and inventory data.

```bash
# Run the spider
uv run python spiers/shfe_spider.py

# Output files:
# - spiers/shfe_spider/data/shfe_settlement.db
# - spiers/shfe_spider/output/settlement_prices.json
```

**Data Extracted:**
- Metal futures settlement prices (copper, aluminum, zinc, etc.)
- Daily trading volumes
- Open interest changes
- Warehouse inventory levels
- Contract specifications

## Database Schema

Each spider creates a SQLite database with specialized schema:

### CISA Production Schema
```sql
CREATE TABLE production_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT,
    indicator_name TEXT,
    value REAL,
    unit TEXT,
    change_rate TEXT,
    source_url TEXT,
    page_category TEXT,
    raw_data TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(report_date, indicator_name, source_url)
);
```

### AMAC Fund Schema
```sql
CREATE TABLE fund_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_date TEXT,
    category TEXT,
    fund_type TEXT,
    institution_name TEXT,
    aum_value REAL,
    num_products INTEGER,
    num_institutions INTEGER,
    unit TEXT,
    change_rate TEXT,
    source_url TEXT,
    raw_data TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(stat_date, category, source_url)
);
```

### SAC Securities Schema
```sql
CREATE TABLE securities_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_date TEXT,
    indicator_name TEXT,
    value REAL,
    unit TEXT,
    change_rate TEXT,
    category TEXT,
    instrument_type TEXT,
    source_url TEXT,
    raw_data TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(stat_date, indicator_name, source_url)
);
```

### CME Agriculture Schema
```sql
CREATE TABLE cme_ag_futures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_date TEXT,
    product_name TEXT,
    settlement_price REAL,
    open_price REAL,
    high_price REAL,
    low_price REAL,
    last_price REAL,
    change REAL,
    change_percent REAL,
    volume INTEGER,
    open_interest INTEGER,
    value REAL,
    unit TEXT,
    source_url TEXT,
    raw_data TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(contract_date, product_name, source_url)
);
```

### SHFE Settlement Schema
```sql
CREATE TABLE shfe_settlement (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT,
    contract_code TEXT,
    product_name TEXT,
    settlement_price REAL,
    reference_price REAL,
    change REAL,
    change_percent REAL,
    open_price REAL,
    high_price REAL,
    low_price REAL,
    close_price REAL,
    last_price REAL,
    volume INTEGER,
    turnover REAL,
    open_interest INTEGER,
    oi_change INTEGER,
    inventory_quantity INTEGER,
    unit TEXT,
    category TEXT,
    source_url TEXT,
    raw_data TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(trade_date, contract_code, source_url)
);
```

## Features

### Common Features Across All Spiders

✅ **Structured HTML Table Extraction** - Parses data tables with automatic header detection  
✅ **Pagination Handling** - Crawls multiple pages and statistic sections  
✅ **SQLite Storage** - Persistent storage with proper schema and upsert support  
✅ **JSON Export** - Clean JSON output for downstream processing  
✅ **Error Handling** - Retry logic and graceful failure handling  
✅ **Rate Limiting** - Respectful delays between requests (2-3 seconds)  
✅ **User Agent Rotation** - Avoids detection with rotating headers  
✅ **Comprehensive Logging** - Detailed execution logs for debugging  

### Source-Specific Features

**CISA Spider:**
- Capacity utilization pattern matching
- Production statistics extraction from multiple report formats
- Monthly/quarterly data normalization

**AMAC Spider:**
- AUM pattern detection (Chinese and English)
- Institution count extraction
- Fund type categorization

**SAC Spider:**
- Trading volume parsing with unit detection
- Broker count estimation
- IPO amount extraction

**CME Agriculture Spider:**
- Multi-product page crawling (corn, wheat, soybeans, livestock)
- Open interest by contract month
- Settlement price normalization
- Contract specification extraction

**SHFE Spider:**
- Metal futures settlement prices (copper, aluminum, zinc, etc.)
- Inventory level tracking
- Contract code parsing
- Warehouse receipt data

## Output Examples

### CISA Production Statistics
```json
[
  {
    "report_date": "2024-06",
    "indicator_name": "crude_steel_production",
    "value": 9280.5,
    "unit": "万吨",
    "change_rate": "+2.3%",
    "source_url": "https://www.cisa.org.cn",
    "raw_data": "{\"headers\": [...], \"row\": [...]}"
  }
]
```

### AMAC Fund Statistics
```json
[
  {
    "stat_date": "2024-06",
    "category": "aum_total",
    "fund_type": "private_equity",
    "aum_value": 185000.0,
    "unit": "亿元",
    "source_url": "https://www.amac.org.cn",
    "raw_data": "AUM pattern matched"
  }
]
```

### CME Agricultural Futures
```json
[
  {
    "contract_date": "2024-06-15",
    "product_name": "Corn Futures",
    "settlement_price": 452.75,
    "open_interest": 1245890,
    "volume": 89234,
    "unit": "USD/bu",
    "source_url": "https://www.cmegroup.com/markets/agricultural/corn.html"
  }
]
```

### SHFE Metal Futures
```json
[
  {
    "trade_date": "2024-06-15",
    "contract_code": "CU2407",
    "product_name": "Copper",
    "settlement_price": 72850.0,
    "volume": 156789,
    "open_interest": 234567,
    "inventory_quantity": 45678,
    "unit": "元/吨",
    "category": "daily_trading"
  }
]
```

## Manifest Files

Each spider has a corresponding manifest.yaml file in `/manifests/`:

- `cisa-production.yaml` - Steel production statistics
- `amac-fund-stats.yaml` - Fund registration and AUM data
- `sac-securities-trading.yaml` - Securities trading volume
- `cme-agricultural-futures.yaml` - Agricultural futures prices
- `shfe-metal-futures.yaml` - Metal futures settlement prices

These manifests define the data schema and can be used for integration with data pipelines.

## Troubleshooting

### Common Issues

**1. No data extracted**
- Check if the website is accessible
- Verify the site structure hasn't changed
- Check logs for HTTP errors

**2. Slow performance**
- Increase `REQUEST_DELAY` in spider config
- Reduce number of concurrent requests

**3. Database errors**
- Ensure `data/` directory exists
- Check disk space
- Verify SQLite permissions

**4. Missing data fields**
- Some sources may not have all fields available
- Check `raw_data` field for original content
- Adjust extraction patterns in spider code

### Debugging

Enable verbose logging:
```python
logging.basicConfig(level=logging.DEBUG)
```

Check extracted data:
```bash
# View JSON output
cat spiers/cisa_spider/output/production_statistics.json | python -m json.tool

# Query SQLite database
sqlite3 spiers/cisa_spider/data/cisa_production.db "SELECT * FROM production_stats LIMIT 10;"
```

## Maintenance

### Updating Extraction Logic

If a source website changes its structure:

1. Identify the affected spider
2. Update the extraction function (e.g., `extract_production_stats()`)
3. Test with sample HTML
4. Update this README if schema changes

### Adding New Sources

To add a new source:

1. Copy an existing spider as template
2. Update URL configuration
3. Modify extraction logic for new site structure
4. Create corresponding manifest.yaml
5. Update this README

## Rate Limiting & Ethics

All spiders implement:
- 2-3 second delays between requests
- Respectful User-Agent headers
- No aggressive crawling
- Compliance with robots.txt (when available)

**Note:** These spiders are for research and data analysis purposes. Always respect the terms of service of target websites.

## License

These spiders are part of the fd-industry-data project. See main project license for details.

## Support

For issues or questions:
- Check the troubleshooting section above
- Review spider logs for error details
- Consult the Scrapyling documentation

---

**Last Updated:** 2024-07-31  
**Total Sources:** 5  
**Total Spiders:** 5  
**Status:** Production Ready
