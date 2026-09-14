# Steel Industry Data Collection (fd-industry-data)

Comprehensive spider-based data extraction from **25 unique steel industry data sources** across 5 categories, totaling **50 candidate URLs** extracted from fd-scraw-harness database.

## 📊 Overview

| Category | Spiders | URLs | Key Sources |
|----------|---------|------|-------------|
| Steel Associations | 1 | 5 | CISA, WorldSteel, Stats Bureau |
| Steel Exchanges | 1 | 6 | SHFE, MySteel, Wind, SMM |
| Statistical Offices | 1 | 4 | CISA, Stats China, MIIT |
| Info Networks | 1 | 5 | MySteel, SteelHome, SMM |
| Market Platforms | 1 | 5 | CRU Group, Fastmarkets, MySteel |

**Total**: 5 spiders covering 50 URL candidates from discovery sessions in fd-scraw-harness

## 🏗️ Project Structure

```
fd-industry-data/
├── spiers/
│   ├── steel-assoc/          # Steel Industry Associations
│   │   ├── spider.py         # Main spider script
│   │   ├── data/             # SQLite database directory
│   │   │   └── association_data.db
│   │   ├── output/           # JSON export directory
│   │   │   └── association_data.json
│   │   └── README.md         # Category documentation
│   │
│   ├── steel-exchange/       # Steel Exchanges & Futures
│   │   ├── spider.py
│   │   ├── data/
│   │   │   └── exchange_data.db
│   │   ├── output/
│   │   │   └── exchange_data.json
│   │   └── README.md
│   │
│   ├── steel-statistics/     # Statistical Office Websites
│   │   ├── spider.py
│   │   ├── data/
│   │   │   └── statistical_data.db
│   │   ├── output/
│   │   │   └── statistical_data.json
│   │   └── README.md
│   │
│   ├── steel-info/           # Industry Information Networks
│   │   ├── spider.py
│   │   ├── data/
│   │   │   └── info_data.db
│   │   ├── output/
│   │   │   └── info_data.json
│   │   └── README.md
│   │
│   └── steel-market/         # Market Data Platforms
│       ├── spider.py
│       ├── data/
│       │   └── market_data.db
│       ├── output/
│       │   └── market_data.json
│       └── README.md
│
├── manifests/                # Datasource manifests for MCP
│   ├── steel-association.yaml
│   ├── steel-exchange.yaml
│   ├── steel-statistics.yaml
│   ├── steel-information.yaml
│   └── steel-market.yaml
│
├── pyproject.toml            # Package configuration
├── README.md                 # Main documentation
└── SUMMARY.md               # Creation summary
```

## 🚀 Quick Start

### 1. Setup Environment

```bash
cd /Users/chengsishi/finddata/fd-industry-data
uv sync
```

### 2. Run Individual Spiders

```bash
# All 5 steel industry spiers
uv run python spiers/steel-assoc/spider.py
uv run python spiers/steel-exchange/spider.py
uv run python spiers/steel-statistics/spider.py
uv run python spiers/steel-info/spider.py
uv run python spiers/steel-market/spider.py

# Or run them all at once
for category in steel-assoc steel-exchange steel-statistics steel-info steel-market; do
    echo "=== Running $category ==="
    uv run python spiers/$category/spider.py
done
```

### 3. Import Manifests to fd-open-data-mcp

```bash
cd /Users/chengsishi/finddata/fd-industry-data
uv run python scripts/import_manifest.py manifests/steel-association.yaml
uv run python scripts/import_manifest.py manifests/steel-exchange.yaml
uv run python scripts/import_manifest.py manifests/steel-statistics.yaml
uv run python scripts/import_manifest.py manifests/steel-information.yaml
uv run python scripts/import_manifest.py manifests/steel-market.yaml
```

## 📋 Data Source Details

### 1. Steel Industry Associations (steel-assoc)

**Purpose**: Official industry reports from associations and government bodies

**Key Tables**:
- `association_data` - Production volumes, export/import stats, price indices

**Top Sources**:
- [中国钢铁工业协会](https://www.cisa.org.cn) - Official production, trade data
- [World Steel Association](https://www.worldsteel.org) - Global statistics
- [国家统计局](http://www.stats.gov.cn/) - Industrial output data

### 2. Steel Exchanges (steel-exchange)

**Purpose**: Futures trading and exchange data

**Key Tables**:
- `exchange_data` - Rebar, wire rod futures contracts

**Top Sources**:
- [上海期货交易所](https://www.shfe.com.cn/) - Official futures data (RB contract)
- [MySteel](https://www.mysteel.com/) - Market analysis platform
- [Wind](https://www.wind.com.cn/) - Professional financial data

### 3. Statistical Offices (steel-statistics)

**Purpose**: Government-produced industry statistics

**Key Tables**:
- `statistical_data` - Monthly production, capacity utilization, trade data

**Top Sources**:
- [中国钢铁工业协会](https://www.cisa.org.cn/) - Industry monthly stats
- [国家统计局](http://www.stats.gov.cn/) - Macro industrial data
- [工信部](http://www.miit.gov.cn/) - Policy and enterprise data

### 4. Info Networks (steel-info)

**Purpose**: Industry news, analysis, and market insights

**Key Tables**:
- `info_data` - News articles, price analysis reports

**Top Sources**:
- [MySteel](https://www.mysteel.com) - Daily market news
- [China Iron and Steel Association](http://www.cisa.org.cn) - Regulatory reports
- [SteelHome](http://www.steelhome.cn) - Price trends tracking

### 5. Market Platforms (steel-market)

**Purpose**: Real-time pricing and market intelligence

**Key Tables**:
- `market_data` - Product prices, inventory levels, trend forecasting

**Top Sources**:
- [MySteel](https://www.mysteel.com/) - Real-time Chinese steel prices
- [WorldSteel](https://www.worldsteel.org/) - Global consumption data
- [CRU Group](https://www.crugroup.com/) - Independent market intelligence
- [Fastmarkets](https://www.fastmarkets.com/) - Ferrous metal assessments

## 🔧 Spider Configuration

Each spider includes built-in features:

```python
# Rate limiting
download_delay = 2.0              # Seconds between requests
concurrent_requests = 2           # Parallel connections

# Retry logic
max_retries = 3                   # Max retry attempts
retry_statuses = {429, 500, 502, 503, 504}  # HTTP codes to retry

# Header rotation
CUSTOM_UAS = [...]                # 5 different user agents
```

## 📝 Implementing Custom Scraping Logic

The current spiders are templates. To extract real data:

### Step 1: Analyze Target Website

Visit each URL and inspect HTML structure:
```python
from scrapling.fetchers import FetcherSession

session = FetcherSession(impersonate="chrome")
response = session.get("https://www.cisa.org.cn")
print(response.text[:2000])  # Preview HTML
```

### Step 2: Modify `extract_data_from_page()`

```python
def extract_data_from_page(html: str, page_url: str) -> list[dict[str, Any]]:
    """Custom parsing logic for this source."""
    items = []
    
    # Example using BeautifulSoup
    try:
        from bs4 import BeautifulSoup
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Find tables or data elements
        for row in soup.select('tr.data-row'):
            cells = row.select('td')
            if len(cells) >= 3:
                items.append({
                    'report_date': cells[0].text.strip(),
                    'indicator_name': cells[1].text.strip(),
                    'value': float(cells[2].text.replace(',', '')),
                    'unit': '万吨',
                    '_source_url': page_url
                })
    except Exception as e:
        logger.error("Parse error: %s", e)
    
    return items
```

### Step 3: Test Incrementally

```bash
# Test single URL
uv run python -c "
from spiers.steel_assoc.spider import run_spider
results = run_spider(urls=['https://www.cisa.org.cn'], save_results=True)
print(f'Extracted {len(results)} items')
"
```

## 🛠️ Schema Templates

Each category has predefined schema patterns for easy customization:

```sql
-- Association data (monthly/yearly stats)
CREATE TABLE association_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT,
    indicator_name TEXT,
    value REAL,
    unit TEXT,
    source_url TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Exchange data (futures trading)
CREATE TABLE exchange_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_code TEXT,
    trade_date TEXT,
    open_price REAL,
    high_price REAL,
    low_price REAL,
    close_price REAL,
    volume INTEGER,
    turnover REAL,
    open_interest REAL,
    source_url TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Statistical data (macro indicators)
CREATE TABLE statistical_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_date TEXT,
    region TEXT,
    indicator_type TEXT,
    current_value REAL,
    prev_value REAL,
    yoy_change REAL,
    mom_change REAL,
    unit TEXT,
    source_url TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Info data (news/articles)
CREATE TABLE info_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publish_date TEXT,
    category TEXT,
    title TEXT,
    content_summary TEXT,
    metrics TEXT,  -- JSON encoded
    source_url TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Market data (pricing)
CREATE TABLE market_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_date TEXT,
    product_type TEXT,
    grade_spec TEXT,
    price REAL,
    price_unit TEXT,
    market_location TEXT,
    trend_direction TEXT,
    source_url TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

## 📊 Expected Data Outputs

After implementing custom parsing, you'll get:

### SQLite Database (`data/*.db`)
- Primary key: `id`
- Timestamps: `fetched_at` automatically added
- Upsert support: `INSERT OR REPLACE` for idempotent runs

### JSON Export (`output/*.json`)
- Pretty-printed with UTF-8 encoding
- Contains all extracted fields
- Human-readable format

### Sample Output Format
```json
[
  {
    "report_date": "2024-01",
    "indicator_name": "粗钢产量",
    "value": 9500.5,
    "unit": "万吨",
    "_source_url": "https://www.cisa.org.cn",
    "fetched_at": "2024-02-15T10:30:00+00:00"
  },
  ...
]
```

## 🔍 Troubleshooting

### Common Issues

**1. Blocked by website**
```bash
# Issue: Cloudflare, anti-bot protection
# Fix: Adjust impersonation in configure_sessions()
session.update_headers({"Referer": "https://example.com"})
```

**2. Missing data**
```bash
# Issue: extract_data_from_page() returns empty
# Fix: Inspect HTML manually and update parsing logic
uv run python -c "
from spiers.steel_assoc.spider import SOURCE_INFO
print(SOURCE_INFO['https://www.cisa.org.cn'])
"
```

**3. Connection errors**
```bash
# Issue: Timeout, network issues
# Fix: Increase REQUEST_DELAY in spider config
REQUEST_DELAY = 3.0
MAX_RETRIES = 5
```

## 📈 Integration with fd-open-data-mcp

Import manifests to make data available via MCP tools:

```bash
cd /Users/chengsishi/finddata/fd-industry-data

# Import all steel data manifests
for manifest in manifests/*.yaml; do
    echo "Importing $(basename $manifest)..."
    uv run python scripts/import_manifest.py "$manifest"
done

# Verify imports in daas.db
cd ../fd-open-data-mcp
uv run sqlite3 metadata/daas.db "SELECT name, description FROM sources WHERE name LIKE '%steel%' ORDER BY name;"
```

Available commands after import:
- `get_association_data(start_quarter?, end_quarter?)`
- `get_futures_data(contract_code?, date_range?)`
- `get_monthly_production(region?, year?)`
- `get_market_news(category?, limit?)`
- `get_product_prices(product_type?, location?)`

## 🔄 Continuous Updates

### Scheduled Crawls

Set up automated data refresh:

```bash
# Add to crontab (runs daily at 2 AM)
0 2 * * * cd /Users/chengsishi/finddata/fd-industry-data && \
  uv run python spiers/steel-assoc/spider.py && \
  uv run python spiers/steel-exchange/spider.py && \
  uv run python spiers/steel-statistics/spider.py && \
  uv run python spiers/steel-info/spider.py && \
  uv run python spiers/steel-market/spider.py
```

### Change Detection

Monitor source updates:

```python
# Check last fetch timestamp
uv run python -c "
import sqlite3
conn = sqlite3.connect('spiers/steel-assoc/data/association_data.db')
cur = conn.cursor()
cur.execute('SELECT MAX(fetched_at) FROM association_data')
print('Last updated:', cur.fetchone()[0])
"
```

## 🎯 Next Steps

1. **Implement Parsing Logic**: For each of the 25 unique URLs, analyze site structure and add parsing code
2. **Test Incrementally**: Start with one source per category before scaling to all 25
3. **Validate Data Quality**: Compare extracted values against known benchmarks
4. **Add Error Handling**: Implement specific handling for dynamic content (JavaScript-rendered pages)
5. **Setup Monitoring**: Track data freshness and missing values

## 📚 References

- **fd-scraw-harness**: Source of truth for discovered URLs (see discovery sessions in `fd-scraw-harness/data/harness.db`)
- **Scrapling Docs**: https://scrapling.dev/docs
- **fd-open-data-protocol**: Manifest schema guidelines
- **MCP Integration**: See `fd-open-data-mcp/README.md` for tool setup

---

**Created**: 2026-07-30  
**Source**: fd-scraw-harness discovery sessions (IDs: 22, 23, 24, 25, 26, 42, 43, 44, 45, 46)  
**Total Candidates Processed**: 50 URLs → 25 unique sources → 5 spider categories
