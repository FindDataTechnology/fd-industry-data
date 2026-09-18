# Steel Exchanges & Data Platforms Spider

Crawls futures exchanges and commodity data platforms for steel trading information.

## Data Sources

| Source | URL | Description |
|--------|-----|-------------|
| 上海期货交易所 | https://www.shfe.com.cn/ | Official futures market: rebar (RB), wire rod contracts |
| 我的钢铁网 | https://www.mysteel.com/ | Leading steel big data platform: prices, inventory, capacity |
| 万得金融终端 | https://www.wind.com.cn/ | Professional financial data: spot/futures markets, macro links |
| 上海有色网 | https://www.smm.cn/ | Ferrous/non-ferrous metal supply chain pricing |
| 钢之家 | http://www.steelhome.cn/ | Production scheduling, export/import, market analysis |
| 国家统计数据库 | http://data.stats.gov.cn/ | Crude steel output, import/export volumes |

## Usage

```bash
cd /Users/chengsishi/finddata/fd-industry-data

# Run spider (all URLs)
uv run python spiers/steel-exchange/spider.py

# Run specific exchange only
uv run python -c "from spiers.steel_exchange.spider import run_spider; run_spider(['https://www.shfe.com.cn/'])"
```

## Output

- **SQLite**: `spiers/steel-exchange/data/exchange_data.db`
- **JSON**: `spiers/steel-exchange/output/exchange_data.json`

## Database Schema

```sql
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
```

## Futures Contract Codes (SHFE)

| Contract | Name | Symbol | Unit |
|----------|------|--------|------|
| 螺纹钢 | Rebar | RB | tons |
| 线材 | Wire Rod | WR | tons |
| 热轧卷板 | Hot Rolled Coil | HC | tons |
| 冷轧 | Cold Rolled Coil | CR | tons |

## Customization Example

```python
def extract_data_from_page(html: str, page_url: str) -> list[dict[str, Any]]:
    """Parse futures data from Shanghai Futures Exchange."""
    items = []
    
    if 'shfe.com.cn' in page_url:
        # SHFE-specific parsing logic here
        pass
    
    return items
```

## Notes

- High-frequency data suitable for daily updates
- Futures prices typically updated at market close
- Volume in lots (1 lot = 10 tons for rebar)
