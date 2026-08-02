# Steel Industry Information Networks Spider

Extracts market intelligence, news, and analysis from industry information portals.

## Data Sources

| Source | URL | Description |
|--------|-----|-------------|
| MySteel | https://www.mysteel.com | Daily prices, production stats, analysis |
| CISA | http://www.cisa.org.cn | Official association statistics & regulatory reports |
| SteelHome | http://www.steelhome.cn | Price trends, inventory levels, market updates |
| SMM | https://www.smm.cn | Ferrous/non-ferrous trading data portal |
| NBS | https://www.stats.gov.cn | Official industrial output statistics |

## Usage

```bash
cd /Users/chengsishi/finddata/fd-industry-data

# Run spider
uv run python spiers/steel-info/spider.py

# Extract specific content types
uv run python -c "from spiers.steel_info.spider import run_spider; run_spider(urls=['https://www.mysteel.com'])"
```

## Output

- **SQLite**: `spiers/steel-info/data/info_data.db`
- **JSON**: `spiers/steel-info/output/info_data.json`

## Database Schema

```sql
CREATE TABLE info_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publish_date TEXT,
    category TEXT,
    title TEXT,
    content_summary TEXT,
    metrics TEXT,
    source_url TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

## Content Categories

- **Market News** - Daily market updates
- **Price Analysis** - Price trend analysis
- **Industry Reports** - Quarterly/yearly reports
- **Policy Updates** - Regulatory changes
- **Production Data** - Company-level data

## Notes

- News aggregation requires RSS/feed parsing
- Some sites block automated access
- Consider manual verification of critical data
