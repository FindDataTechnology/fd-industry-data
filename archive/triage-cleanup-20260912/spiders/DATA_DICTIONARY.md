# Data Dictionary - Industry Association & Commodity Exchange Scrapers

This document provides detailed field definitions for all data extracted by the 5 specialized scrapers.

---

## 1. CISA Production Statistics (cisa_spider.py)

### Table: `production_stats`

| Field | Type | Description | Example | Notes |
|-------|------|-------------|---------|-------|
| `id` | INTEGER | Primary key | 1 | Auto-increment |
| `report_date` | TEXT | Report period | "2024-06" | Format: YYYY-MM or YYYY-MM-DD |
| `indicator_name` | TEXT | Metric name | "crude_steel_production" | Standardized indicator names |
| `value` | REAL | Numeric value | 9280.5 | Production volume, rate, etc. |
| `unit` | TEXT | Measurement unit | "万吨" | Chinese units (万吨，%) |
| `change_rate` | TEXT | Period-over-period change | "+2.3%" | Can be positive or negative |
| `source_url` | TEXT | Data source URL | "https://www.cisa.org.cn/..." | Full URL of source page |
| `page_category` | TEXT | Page type | "industry-production" | Categorization tag |
| `raw_data` | TEXT | Original extraction | "{\"headers\": [...], \"row\": [...]}" | JSON string with raw data |
| `fetched_at` | TEXT | Extraction timestamp | "2024-07-31T10:30:00" | ISO 8601 format |

### Common Indicators

| Indicator Name | Description | Typical Unit |
|----------------|-------------|--------------|
| `crude_steel_production` | Monthly crude steel output | 万吨 |
| `pig_iron_production` | Pig iron production volume | 万吨 |
| `steel_material_production` | Steel materials output | 万吨 |
| `capacity_utilization_rate` | Capacity utilization percentage | % |
| `steel_export_volume` | Steel export quantity | 万吨 |
| `steel_import_volume` | Steel import quantity | 万吨 |
| `steel_price_index` | Steel price index value | points |

---

## 2. AMAC Fund Statistics (mac_spider.py)

### Table: `fund_stats`

| Field | Type | Description | Example | Notes |
|-------|------|-------------|---------|-------|
| `id` | INTEGER | Primary key | 1 | Auto-increment |
| `stat_date` | TEXT | Statistics date | "2024-06" | Format: YYYY-MM |
| `category` | TEXT | Data category | "aum_total" | See categories below |
| `fund_type` | TEXT | Fund classification | "private_equity" | Fund type descriptor |
| `institution_name` | TEXT | Institution name | "" | Usually empty for aggregates |
| `aum_value` | REAL | Assets under management | 185000.0 | In 亿元 (100M yuan) |
| `num_products` | INTEGER | Number of fund products | 12450 | Total product count |
| `num_institutions` | INTEGER | Number of institutions | 2450 | Registered managers |
| `unit` | TEXT | Measurement unit | "亿元" | Chinese units |
| `change_rate` | TEXT | Period change | "+5.2%" | Growth rate |
| `source_url` | TEXT | Data source URL | "https://www.amac.org.cn/..." | Source page |
| `raw_data` | TEXT | Original extraction | "AUM pattern matched: ..." | Extraction context |
| `fetched_at` | TEXT | Extraction timestamp | "2024-07-31T10:30:00" | ISO 8601 format |

### Category Values

| Category | Description |
|----------|-------------|
| `aum_total` | Total assets under management |
| `institution_count` | Number of registered institutions |
| `product_count` | Number of fund products |
| `general_stats` | Other statistical metrics |

### Fund Type Values

| Fund Type | Description |
|-----------|-------------|
| `private_equity` | Private equity funds |
| `private_equity_manager` | PE fund managers |
| `securities_fund` | Public securities funds |
| `venture_capital` | Venture capital funds |
| `all` | All fund types combined |

---

## 3. SAC Securities Trading (sac_spider.py)

### Table: `securities_stats`

| Field | Type | Description | Example | Notes |
|-------|------|-------------|---------|-------|
| `id` | INTEGER | Primary key | 1 | Auto-increment |
| `stat_date` | TEXT | Statistics date | "2024-06-15" | Format: YYYY-MM-DD |
| `indicator_name` | TEXT | Metric name | "trading_volume" | See indicators below |
| `value` | REAL | Numeric value | 1245.8 | Volume, amount, count |
| `unit` | TEXT | Measurement unit | "亿股" | Chinese units |
| `change_rate` | TEXT | Period change | "" | Often empty |
| `category` | TEXT | Data category | "trading_volume" | Categorization |
| `instrument_type` | TEXT | Security type | "total_market" | Instrument classification |
| `source_url` | TEXT | Data source URL | "https://www.sac.net.cn/..." | Source page |
| `raw_data` | TEXT | Original extraction | "{\"cells\": [...]}" | Raw table data |
| `fetched_at` | TEXT | Extraction timestamp | "2024-07-31T10:30:00" | ISO 8601 format |

### Indicator Names

| Indicator | Description | Typical Unit |
|-----------|-------------|--------------|
| `trading_volume` | Securities trading volume | 亿股 |
| `trading_turnover` | Trading turnover amount | 亿元 |
| `trade_count` | Number of transactions | 笔 |
| `broker_count` | Number of brokerage firms | 家 |
| `total_assets` | Total industry assets | 元 |
| `ipo_amount` | IPO transaction amount | 元 |
| `market_volume` | Market-wide trading volume | 亿元 |

### Instrument Types

| Type | Description |
|------|-------------|
| `total_market` | Entire market aggregate |
| `securities_company` | Individual broker |
| `ipo_transaction` | IPO-related |
| `a_share` | A-share market |
| `bond` | Bond market |
| `fund` | Fund market |

---

## 4. CME Agricultural Futures (cmegroup_ag_spider.py)

### Table: `cme_ag_futures`

| Field | Type | Description | Example | Notes |
|-------|------|-------------|---------|-------|
| `id` | INTEGER | Primary key | 1 | Auto-increment |
| `contract_date` | TEXT | Contract/trade date | "2024-06-15" | Format: YYYY-MM-DD |
| `product_name` | TEXT | Commodity name | "Corn Futures" | Full product name |
| `settlement_price` | REAL | Daily settlement price | 452.75 | USD per bushel/contract |
| `open_price` | REAL | Opening price | 450.25 | USD |
| `high_price` | REAL | Daily high price | 455.50 | USD |
| `low_price` | REAL | Daily low price | 448.75 | USD |
| `last_price` | REAL | Last traded price | 453.00 | USD |
| `change` | REAL | Price change | +2.50 | USD change from previous |
| `change_percent` | REAL | Percentage change | 0.55 | % change |
| `volume` | INTEGER | Trading volume | 89234 | Number of contracts |
| `open_interest` | INTEGER | Open interest | 1245890 | Outstanding contracts |
| `value` | REAL | Contract value | NULL | Calculated value |
| `unit` | TEXT | Price unit | "USD/bu" | USD per bushel/contract |
| `source_url` | TEXT | Data source URL | "https://www.cmegroup.com/..." | Source page |
| `raw_data` | TEXT | Original extraction | "{\"cells\": [...]}" | Raw table data |
| `fetched_at` | TEXT | Extraction timestamp | "2024-07-31T10:30:00" | ISO 8601 format |

### Product Names

| Product | Description | Unit |
|---------|-------------|------|
| `Corn Futures` | Corn commodity futures | USD/bu |
| `Wheat Futures` | Wheat commodity futures | USD/bu |
| `Soybeans Futures` | Soybean commodity futures | USD/bu |
| `Live Cattle Futures` | Live cattle futures | USD/cwt |
| `Lean Hogs Futures` | Lean hog futures | USD/cwt |
| `Class III Milk Futures` | Dairy product futures | USD/cwt |
| `Cotton Futures` | Cotton commodity futures | USD/lb |

### Unit Definitions

| Unit | Meaning |
|------|---------|
| `USD/bu` | US dollars per bushel |
| `USD/cwt` | US dollars per hundredweight |
| `USD/lb` | US dollars per pound |
| `contracts` | Number of futures contracts |

---

## 5. SHFE Metal Futures (shfe_spider.py)

### Table: `shfe_settlement`

| Field | Type | Description | Example | Notes |
|-------|------|-------------|---------|-------|
| `id` | INTEGER | Primary key | 1 | Auto-increment |
| `trade_date` | TEXT | Trading date | "2024-06-15" | Format: YYYY-MM-DD |
| `contract_code` | TEXT | Futures contract code | "CU2407" | Exchange code + expiry |
| `product_name` | TEXT | Metal/commodity name | "Copper" | Product type |
| `settlement_price` | REAL | Settlement price | 72850.0 | Yuan per ton |
| `reference_price` | REAL | Reference price | 72800.0 | Previous settlement |
| `change` | REAL | Price change | +50.0 | Yuan change |
| `change_percent` | REAL | Percentage change | 0.07 | % change |
| `open_price` | REAL | Opening price | 72750.0 | Yuan/ton |
| `high_price` | REAL | Daily high | 73100.0 | Yuan/ton |
| `low_price` | REAL | Daily low | 72600.0 | Yuan/ton |
| `close_price` | REAL | Closing price | 72900.0 | Yuan/ton |
| `last_price` | REAL | Last traded price | 72850.0 | Yuan/ton |
| `volume` | INTEGER | Trading volume | 156789 | Number of contracts |
| `turnover` | REAL | Trading turnover | NULL | Yuan value |
| `open_interest` | INTEGER | Open interest | 234567 | Outstanding contracts |
| `oi_change` | INTEGER | OI change | +1234 | Change in open interest |
| `inventory_quantity` | INTEGER | Warehouse inventory | 45678 | Tons in warehouse |
| `unit` | TEXT | Price unit | "元/吨" | Yuan per ton |
| `category` | TEXT | Data category | "daily_trading" | See categories below |
| `source_url` | TEXT | Data source URL | "https://www.shfe.com.cn/..." | Source page |
| `raw_data` | TEXT | Original extraction | "{\"cells\": [...]}" | Raw table data |
| `fetched_at` | TEXT | Extraction timestamp | "2024-07-31T10:30:00" | ISO 8601 format |

### Contract Code Format

| Code | Meaning | Example |
|------|---------|---------|
| `CU2407` | Copper July 2024 contract | CU = Copper, 24 = 2024, 07 = July |
| `AL2408` | Aluminum August 2024 | AL = Aluminum |
| `ZN2406` | Zinc June 2024 | ZN = Zinc |
| `PB2409` | Lead September 2024 | PB = Lead |
| `NI2410` | Nickel October 2024 | NI = Nickel |
| `SN2411` | Tin November 2024 | SN = Tin |
| `RB2412` | Rebar December 2024 | RB = Rebar |
| `RU2501` | Rubber January 2025 | RU = Rubber |

### Product Names

| Product | Chinese | Description |
|---------|---------|-------------|
| `Copper` | 铜 | Copper futures |
| `Aluminum` | 铝 | Aluminum futures |
| `Zinc` | 锌 | Zinc futures |
| `Lead` | 铅 | Lead futures |
| `Nickel` | 镍 | Nickel futures |
| `Tin` | 锡 | Tin futures |
| `Rebar` | 螺纹钢 | Reinforcing steel bar |
| `Hot Rolled Coil` | 热轧卷板 | Hot rolled steel coil |
| `Natural Rubber` | 天然橡胶 | Natural rubber futures |
| `Bitumen` | 沥青 | Asphalt/bitumen futures |

### Category Values

| Category | Description |
|----------|-------------|
| `daily_trading` | Daily trading data |
| `inventory` | Warehouse inventory levels |
| `contract_spec` | Contract specifications |
| `settlement` | Settlement prices only |

### Unit Definitions

| Unit | Meaning |
|------|---------|
| `元/吨` | Chinese Yuan per metric ton |
| `公吨` | Metric tons (for inventory) |
| `手` | Lots/contracts (for volume) |

---

## Data Type Conventions

### Numeric Fields
- **REAL**: Floating-point numbers (prices, rates, percentages)
- **INTEGER**: Whole numbers (counts, volumes, quantities)
- **TEXT**: String values (dates, codes, names)

### Date Formats
- **YYYY-MM**: Month-level granularity (e.g., "2024-06")
- **YYYY-MM-DD**: Day-level granularity (e.g., "2024-06-15")
- **ISO 8601**: Full timestamp (e.g., "2024-07-31T10:30:00")

### Null Values
- Empty strings `""` for missing text
- `NULL` for missing numeric values
- Check with `WHERE field IS NOT NULL` in queries

### Units
- **Chinese units**: 万吨 (10,000 tons), 亿元 (100M yuan), 家 (institutions), 只 (funds)
- **International units**: USD/bu, USD/cwt, contracts
- **Mixed**: Some sources use both Chinese and international units

---

## Query Examples

### CISA - Get Latest Production Stats
```sql
SELECT report_date, indicator_name, value, unit, change_rate
FROM production_stats
WHERE indicator_name = 'crude_steel_production'
ORDER BY report_date DESC
LIMIT 10;
```

### AMAC - Get AUM by Fund Type
```sql
SELECT stat_date, fund_type, aum_value, unit
FROM fund_stats
WHERE category = 'aum_total'
  AND aum_value IS NOT NULL
ORDER BY stat_date DESC;
```

### SAC - Get Trading Volume Trends
```sql
SELECT stat_date, value, unit
FROM securities_stats
WHERE indicator_name = 'trading_volume'
  AND instrument_type = 'total_market'
ORDER BY stat_date DESC
LIMIT 30;
```

### CME - Get Corn Futures Prices
```sql
SELECT contract_date, settlement_price, volume, open_interest
FROM cme_ag_futures
WHERE product_name = 'Corn Futures'
  AND settlement_price IS NOT NULL
ORDER BY contract_date DESC;
```

### SHFE - Get Copper Settlement Prices
```sql
SELECT trade_date, contract_code, settlement_price, volume, open_interest
FROM shfe_settlement
WHERE product_name = 'Copper'
  AND category = 'daily_trading'
ORDER BY trade_date DESC
LIMIT 30;
```

---

## Data Quality Notes

### Completeness
- Not all fields are populated for every record
- Some sources provide more granular data than others
- Check `raw_data` field for original extraction context

### Accuracy
- Prices are as reported by source websites
- Settlement prices are official exchange values
- Volume and OI data may have reporting delays

### Timeliness
- Data is extracted at crawl time
- Some sources update daily, others monthly
- Check `fetched_at` timestamp for freshness

### Consistency
- Units are standardized per source
- Chinese sources use Chinese units
- International sources use metric/US customary units

---

## Integration Guidelines

### Loading into Pandas
```python
import pandas as pd
import sqlite3

# Load CISA data
conn = sqlite3.connect('spiers/cisa_spider/data/cisa_production.db')
df = pd.read_sql('SELECT * FROM production_stats', conn)
conn.close()

# Parse dates
df['report_date'] = pd.to_datetime(df['report_date'])

# Filter and analyze
recent = df[df['report_date'] > '2024-01-01']
```

### JSON Processing
```python
import json

# Load JSON output
with open('spiers/cisa_spider/output/production_statistics.json') as f:
    data = json.load(f)

# Convert to DataFrame
df = pd.DataFrame(data)
```

### Data Validation
```python
# Check for nulls
print(df.isnull().sum())

# Check value ranges
print(df['value'].describe())

# Check date ranges
print(f"Date range: {df['report_date'].min()} to {df['report_date'].max()}")
```

---

**Last Updated:** 2024-07-31  
**Version:** 1.0  
**Maintained by:** fd-industry-data project
