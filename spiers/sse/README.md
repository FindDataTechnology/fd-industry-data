# Shanghai Stock Exchange (SSE) Spider

Crawls the Shanghai Stock Exchange for stock market trading data.

## Data Source

| Source | URL | Score |
|--------|-----|-------|
| 上海证券交易所 | https://www.sse.com.cn | 90 |

## Data Extracted

- **Market Overview** (市场概览) - stock/bond/fund trading summaries
- **Trading Volume** (交易数据) - volume, turnover, price indices
- **Bond Trading** (债券交易) - government bonds, corporate bonds, convertible bonds
- **Fund Trading** (基金交易) - ETF, LOF, closed-end funds

## Usage

```bash
cd ~/finddata/fd-industry-data
uv run python spiers/sse/spider.py
```

## Output

- **SQLite**: `spiers/sse/data/sse_market_data.db`
- **JSON**: `spiers/sse/output/sse_trading_data.json`

## Database Schema

```sql
CREATE TABLE sse_trading (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_date TEXT,
    indicator_name TEXT,
    value REAL,
    unit TEXT,
    change_rate TEXT,
    category TEXT,           -- market_overview, trading_volume, trading_turnover, price_index, bond_trading, fund_trading
    instrument_type TEXT,
    source_url TEXT,
    raw_data TEXT,
    fetched_at TEXT NOT NULL
);
```

## Notes

- Data is publicly available from the Shanghai Stock Exchange
- Covers stocks, bonds, and funds traded on SSE
- Daily updates recommended for trading volume and price data
