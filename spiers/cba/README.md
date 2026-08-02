# China Banking Association (CBA) Spider

Crawls the China Banking Association for banking industry statistics.

## Data Source

| Source | URL | Score |
|--------|-----|-------|
| 中国银行业协会 | https://www.chinabankass.org.cn | 92 |

## Data Extracted

- **Banking Statistics** (银行业统计数据) - total assets, liabilities, profits
- **Asset Quality** (资产质量) - NPL ratios, provision coverage ratios
- **Capital Adequacy** (资本充足率) - CAR ratios across bank types
- **Liquidity Metrics** (流动性指标) - liquidity ratio, LCR

## Usage

```bash
cd ~/finddata/fd-industry-data
uv run python spiers/cba/spider.py
```

## Output

- **SQLite**: `spiers/cba/data/cba_banking_data.db`
- **JSON**: `spiers/cba/output/banking_statistics.json`

## Database Schema

```sql
CREATE TABLE banking_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_date TEXT,
    indicator_name TEXT,
    value REAL,
    unit TEXT,
    change_rate TEXT,
    category TEXT,           -- balance_sheet, asset_quality, profitability, solvency
    bank_type TEXT,          -- commercial_bank, policy_bank, etc.
    source_url TEXT,
    raw_data TEXT,
    fetched_at TEXT NOT NULL
);
```

## Notes

- Data is publicly available from the China Banking Association
- Covers commercial banks, policy banks, and other financial institutions
- Quarterly updates recommended for regulatory statistics
