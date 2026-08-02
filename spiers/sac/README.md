# China Securities Association (SAC) Spider

Crawls the Securities Association of China for securities industry statistics.

## Data Source

| Source | URL | Score |
|--------|-----|-------|
| 中国证券业协会 | https://www.sac.net.cn | 95 |

## Data Extracted

- **Securities Trading Volume** (证券交易数据) - volume, turnover, transaction counts
- **Broker Statistics** (券商统计) - number of securities companies, total assets
- **Market Indices** (市场指数) - composite index, trading amounts
- **IPO Data** (IPO数据) - issuance amounts, underwriting data

## Usage

```bash
cd ~/finddata/fd-industry-data
uv run python spiers/sac/spider.py
```

## Output

- **SQLite**: `spiers/sac/data/sac_securities_data.db`
- **JSON**: `spiers/sac/output/securities_trading.json`

## Database Schema

```sql
CREATE TABLE securities_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_date TEXT,
    indicator_name TEXT,
    value REAL,
    unit TEXT,
    change_rate TEXT,
    category TEXT,           -- trading_volume, trading_turnover, institution_count, investment_banking
    instrument_type TEXT,
    source_url TEXT,
    raw_data TEXT,
    fetched_at TEXT NOT NULL
);
```

## Notes

- High-scored source (95) - authoritative securities industry data
- Covers trading data for stocks, bonds, and funds across Chinese exchanges
- Monthly updates recommended for trading statistics
