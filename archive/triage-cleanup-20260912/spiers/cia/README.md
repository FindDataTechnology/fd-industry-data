# China Insurance Association (CIA) Spider

Crawls the China Insurance Association for insurance market statistics.

## Data Source

| Source | URL | Score |
|--------|-----|-------|
| 中国保险行业协会 | https://www.iachina.cn | 90 |

## Data Extracted

- **Premium Income** (保费收入) - by insurance type (life, property, health, accident)
- **Market Overview** - total assets, claims paid, company count
- **Solvency Ratios** - comprehensive and core solvency adequacy ratios
- **Product Distribution** - premium breakdown by insurance product line

## Usage

```bash
cd ~/finddata/fd-industry-data
uv run python spiers/cia/spider.py
```

## Output

- **SQLite**: `spiers/cia/data/cia_insurance_data.db`
- **JSON**: `spiers/cia/output/insurance_market_data.json`

## Database Schema

```sql
CREATE TABLE insurance_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_date TEXT,
    indicator_name TEXT,
    value REAL,
    unit TEXT,
    change_rate TEXT,
    category TEXT,           -- premium, balance_sheet, claims, solvency, product_distribution
    insurance_type TEXT,     -- life, property, health, accident, total
    source_url TEXT,
    raw_data TEXT,
    fetched_at TEXT NOT NULL
);
```

## Notes

- Data is publicly available from the China Insurance Association
- Insurance types: 寿险 (life), 财险 (property), 健康险 (health), 意外险 (accident)
- Solvency data follows C-ROSS regulatory framework
