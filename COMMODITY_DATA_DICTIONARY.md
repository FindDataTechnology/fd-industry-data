# Commodity Spiders Data Dictionary

## Overview

This document provides a comprehensive data dictionary for all Chinese commodity exchange spiders implemented in the `fd-industry-data` package.

---

## 1. SHFE Spider (Shanghai Futures Exchange)

### Database: `data/shfe_prices.db`

#### Table: `steel_futures`
**Description:** Steel futures pricing and volume data

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| scraped_at | TEXT | ISO timestamp of scrape |
| source_url | TEXT | Original data source URL |
| category | TEXT | Data category (steel_futures) |
| data | JSONB | Full item data as JSON |

**JSON Fields:**
- `commodity_name` - Product name (e.g., 螺纹钢, 线材, 热卷)
- `ticker` - Future contract code
- `settlement_price` - Daily settlement price
- `change` - Price change
- `volume` - Trading volume
- `open_interest` - Open positions

#### Table: `industrial_metals`
**Description:** Industrial metals pricing data

**JSON Fields:**
- `commodity_name` - Metal name (铜, 铝, 锌, 铅, 镍, 锡)
- `ticker` - Contract code
- `opening_price`, `highest_price`, `lowest_price`, `closing_price`
- `settlement_price` - Daily settlement
- `change_pct` - Percentage change
- `volume`, `open_interest`

#### Table: `energy_products`
**Description:** Energy product pricing data

**JSON Fields:**
- `commodity_name` - Product name (燃料油, 原油, 汽油, 沥青)
- `ticker` - Contract code
- `settlement_price`, `change`, `volume`, `open_interest`

#### Table: `rubber_precious`
**Description:** Rubber and precious metals pricing

**JSON Fields:**
- `commodity_name` - Product name (天然橡胶, 黄金, 白银)
- `ticker`, `settlement_price`, `change`, `volume`, `open_interest`

---

## 2. DCE Spider (Dalian Commodity Exchange)

### Database: `data/dce_prices.db`

#### Table: `agriculture`
**Description:** Agricultural product pricing and volume data

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| scraped_at | TEXT | ISO timestamp |
| source_url | TEXT | Source URL |
| category | TEXT | Category (agriculture) |
| contract_name | TEXT | Contract name |
| opening | REAL | Opening price |
| high | REAL | Highest price |
| low | REAL | Lowest price |
| close | REAL | Closing price |
| settlement | REAL | Settlement price |
| change | TEXT | Price change |
| volume | REAL | Trading volume |
| open_interest | REAL | Open interest |
| other_data | JSONB | Additional fields |

**Products Covered:**
- 大豆 A (Soybeans A)
- 豆粕 (Soybean Meal)
- 玉米 (Corn)
- 棕榈油 (Palm Oil)

#### Table: `chemicals`
**Description:** Chemical product pricing data

**Products Covered:**
- PP (聚丙烯)
- PE (聚乙烯)
- PVC (聚氯乙烯)
- 苯乙烯 (Styrene)

#### Table: `livestock`
**Description:** Livestock futures pricing data

**Products Covered:**
- 生猪 (Live Hogs)

#### Table: `general`
**Description:** General futures data

#### Table: `analysis`
**Description:** Market analysis articles and reports

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| scraped_at | TEXT | ISO timestamp |
| source_url | TEXT | Source URL |
| category | TEXT | Category (analysis) |
| title | TEXT | Article title |
| url | TEXT | Article URL |
| publish_date | TEXT | Publication date |
| content | TEXT | Article content |
| other_data | JSONB | Additional fields |

---

## 3. ZCE Spider (Zhengzhou Commodity Exchange)

### Database: `data/zce_prices.db`

#### Table: `agricultural_spot`
**Description:** Agricultural spot prices

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| scraped_at | TEXT | ISO timestamp |
| source_url | TEXT | Source URL |
| category | TEXT | Category |
| price_type | TEXT | Price type (spot) |
| contract_name | TEXT | Contract name |
| opening | REAL | Opening price |
| high | REAL | Highest price |
| low | REAL | Lowest price |
| close | REAL | Closing price |
| settlement | REAL | Settlement price |
| change | TEXT | Price change |
| volume | REAL | Trading volume |
| open_interest | REAL | Open interest |
| other_data | JSONB | Additional fields |

**Products Covered:**
- 棉花 (Cotton)
- 白糖 (Sugar)
- PTA
- 甲醇 (Methanol)
- PP (聚丙烯)
- 苹果 (Apple)
- 红枣 (Jujube)

#### Table: `agricultural_futures`
**Description:** Agricultural futures pricing data

#### Table: `chemicals_spot`
**Description:** Chemical product spot prices

**Products Covered:**
- 短纤 (Short fiber)
- 聚酯链 (Polyester chain)
- CA (共聚甲醛)

#### Table: `chemicals_futures`
**Description:** Chemical product futures pricing

#### Table: `coal_coke_spot`
**Description:** Coal and coke spot prices

**Products Covered:**
- 动力煤 (Thermal coal)
- 焦炭 (Coke)
- 甲醇 (Methanol)

#### Table: `coal_coke_futures`
**Description:** Coal and coke futures pricing

#### Table: `market_overview`
**Description:** Market overview statistics

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| scraped_at | TEXT | ISO timestamp |
| source_url | TEXT | Source URL |
| category | TEXT | Category (market_overview) |
| title | TEXT | Overview title |
| data | JSONB | Statistics data |

#### Table: `economic_news`
**Description:** Economic news articles

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| scraped_at | TEXT | ISO timestamp |
| source_url | TEXT | Source URL |
| category | TEXT | Category (economic_news) |
| title | TEXT | Article title |
| url | TEXT | Article URL |
| publish_date | TEXT | Publication date |
| type | TEXT | Article type |
| content | TEXT | Article content |

---

## 4. CISA Spider (China Iron & Steel Association)

### Database: `data/cisa_data.db`

#### Table: `production`
**Description:** Production statistics and output data

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| scraped_at | TEXT | ISO timestamp |
| source_url | TEXT | Source URL |
| category | TEXT | Category (production) |
| title | TEXT | Report title |
| url | TEXT | Report URL |
| publish_date | TEXT | Publication date |
| content | TEXT | Report content |
| other_data | JSONB | Additional fields |

**Data Types:**
- Production output volumes
- Production efficiency metrics
- Capacity utilization rates

#### Table: `trade`
**Description:** Import/export trade data

**Data Types:**
- Import volumes and values
- Export volumes and values
- Trade balance by product

#### Table: `price`
**Description:** Price indices and regional pricing data

**Data Types:**
- Official CISA price indices
- Regional price variations
- Product variety prices

#### Table: `analysis`
**Description:** Industry analysis reports and market outlook

**Data Types:**
- In-depth analysis reports
- Market condition assessments
- Future market projections

#### Table: `general`
**Description:** General steel industry data

---

## Common Fields

All tables share these standard fields:

| Field | Type | Description |
|-------|------|-------------|
| id | INTEGER | Auto-incrementing primary key |
| scraped_at | TEXT | ISO 8601 timestamp (YYYY-MM-DDTHH:MM:SS) |
| source_url | TEXT | Original URL where data was extracted |
| category | TEXT | Data category classification |

---

## JSON Export Format

Each table exports to JSON with this structure:

```json
[
  {
    "id": 1,
    "scraped_at": "2026-07-31T16:30:00",
    "source_url": "https://www.shfe.com.cn/data/...",
    "category": "steel_futures",
    "data": {
      "commodity_name": "螺纹钢",
      "ticker": "RB2410",
      "settlement_price": 3850.0,
      "change": 25.0,
      "volume": 125000,
      "open_interest": 98000
    }
  }
]
```

---

## Data Types Reference

### Price Fields
- `opening`, `high`, `low`, `close`, `settlement` - REAL (numeric)
- `change` - TEXT (may include +/- signs and units)
- `change_pct` - REAL (percentage)

### Volume Fields
- `volume` - REAL (trading volume in contracts/lots)
- `open_interest` - REAL (outstanding contracts)

### Date Fields
- `scraped_at` - TEXT (ISO 8601 format)
- `publish_date` - TEXT (various formats)

### Text Fields
- `title`, `content`, `url` - TEXT (UTF-8 encoded)

### JSON Fields
- `data`, `other_data` - JSONB (flexible schema)

---

## Unit Conversions

Some Chinese sources use special units:
- **万 (wan)** = 10,000
- **亿 (yi)** = 100,000,000

These are automatically converted to standard numeric values during extraction.

---

## Data Quality Notes

1. **Null Values:** Empty cells are stored as NULL or empty strings
2. **Deduplication:** UNIQUE constraints prevent duplicate entries
3. **Encoding:** All text is UTF-8 encoded for Chinese characters
4. **Timestamps:** All timestamps use ISO 8601 format
5. **Numeric Precision:** Prices stored as REAL (double precision)

---

## Accessing Data

### SQLite Query Example

```sql
-- Get latest SHFE steel futures
SELECT * FROM steel_futures 
ORDER BY scraped_at DESC 
LIMIT 10;

-- Get DCE agricultural products by date
SELECT * FROM agriculture 
WHERE scraped_at >= '2026-07-01'
ORDER BY scraped_at DESC;

-- Get CISA production reports
SELECT title, publish_date, content 
FROM production 
WHERE category = 'production'
ORDER BY publish_date DESC;
```

### Python Access Example

```python
import sqlite3
import json

# Connect to database
conn = sqlite3.connect('spiders/shfe/data/shfe_prices.db')
conn.row_factory = sqlite3.Row

# Query data
rows = conn.execute("SELECT * FROM steel_futures ORDER BY id DESC LIMIT 5").fetchall()

for row in rows:
    data = json.loads(row['data'])
    print(f"{data['commodity_name']}: {data['settlement_price']}")

conn.close()
```

---

## Maintenance

### Regular Tasks
1. **Monitor row counts** - Ensure data is being collected
2. **Check timestamps** - Verify recent scrapes
3. **Validate JSON exports** - Ensure output files are valid
4. **Review error logs** - Check for scraping failures

### Data Retention
- SQLite databases grow over time
- Consider archiving old data (> 1 year)
- Implement cleanup scripts for production use

---

## Version History

- **v1.0.0** (2026-07-31) - Initial implementation
  - SHFE, DCE, ZCE, CISA spiders
  - SQLite storage with JSON export
  - Comprehensive anti-bot protection

---

## Support

For questions about data fields or schema:
1. Check individual spider README files
2. Review manifest.yaml configurations
3. Examine spider.py source code
4. Consult test output for examples
