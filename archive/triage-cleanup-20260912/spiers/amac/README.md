# AMAC (Asset Management Association of China) Spider

Crawls the Asset Management Association of China for fund industry statistics.

## Data Source

| Source | URL | Score |
|--------|-----|-------|
| 中国证券投资基金业协会 | https://www.amac.org.cn | 98 |

## Data Extracted

- **Fund Manager Registration** (基金管理人注册) - number of registered managers
- **AUM Statistics** (资产管理规模) - total assets under management
- **Institution Counts** (机构数量) - private equity managers, public fund companies
- **Product Counts** (产品数量) - registered fund products by type

## Usage

```bash
cd ~/finddata/fd-industry-data
uv run python spiers/amac/spider.py
```

## Output

- **SQLite**: `spiers/amac/data/amac_fund_data.db`
- **JSON**: `spiers/amac/output/fund_statistics.json`

## Database Schema

```sql
CREATE TABLE fund_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_date TEXT,
    category TEXT,           -- aum_total, institution_count, product_count, general_stats
    fund_type TEXT,          -- private_equity, public_fund, etc.
    institution_name TEXT,
    aum_value REAL,
    num_products INTEGER,
    num_institutions INTEGER,
    unit TEXT,
    change_rate TEXT,
    source_url TEXT,
    raw_data TEXT,
    fetched_at TEXT NOT NULL
);
```

## Notes

- Highest-scored source (98) - authoritative fund industry data
- Data covers both public and private fund sectors
- Monthly updates recommended for AUM and institution counts
