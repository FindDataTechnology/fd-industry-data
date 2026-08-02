# Steel Market Data Platforms Spider

Real-time market pricing and analysis from leading commodity data platforms.

## Data Sources

| Source | URL | Description |
|--------|-----|-------------|
| MySteel | https://www.mysteel.com/ | Real-time prices, inventory, production data |
| World Steel Association | https://www.worldsteel.org/ | Global production/consumption/trade statistics |
| CRU Group | https://www.crugroup.com/ | Market intelligence, price assessments, forecasting |
| Fastmarkets | https://www.fastmarkets.com/ | Ferrous metal pricing, news, analytics |
| NBS | http://www.stats.gov.cn/ | Verified steel volumes, import/export stats |

## Usage

```bash
cd /Users/chengsishi/finddata/fd-industry-data

# Run spider
uv run python spiers/steel-market/spider.py

# All market platforms
uv run python -c "from spiers.steel_market.spider import run_spider; run_spider()"
```

## Output

- **SQLite**: `spiers/steel-market/data/market_data.db`
- **JSON**: `spiers/steel-market/output/market_data.json`

## Database Schema

```sql
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

## Product Types Covered

1. **Rebar (螺纹钢)** - Construction reinforcement
2. **Hot Rolled Coil (热卷)** - Manufacturing feedstock
3. **Wire Rod (线材)** - Drawing applications
4. **Medium Plate (中板)** - Heavy equipment
5. **Steel Pipe (管材)** - Pipeline/construction

## Price Indicators

- **Local Market Price** - Regional spot prices
- **Futures Reference** - SHFE benchmark prices
- **Import Parity** - CIF China prices
- **Export Parity** - FOB export prices

## Notes

- Premium data services may require authentication
- Free tier limits: ~100 requests/day
- Historical data often behind paywall
- Cross-validate with multiple sources
