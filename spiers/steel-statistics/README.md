# Steel Statistical Office Websites Spider

Extracts statistical data from official government and industry statistics bureaus.

## Data Sources

| Source | URL | Description |
|--------|-----|-------------|
| 中国钢铁工业协会 | https://www.cisa.org.cn/ | Monthly production, capacity utilization, import/export stats |
| 国家统计局 | http://www.stats.gov.cn/ | Industrial statistics for black metal smelting industry |
| 工业和信息化部 | http://www.miit.gov.cn/ | Industry operation data and policy information |
| 世界钢铁协会 | http://www.worldsteel.org/zh-hans/ | Global production & demand by major producing countries |

## Usage

```bash
cd /Users/chengsishi/finddata/fd-industry-data

# Run spider
uv run python spiers/steel-statistics/spider.py

# Specific indicators
uv run python -c "from spiers.steel_statistics.spider import run_spider; run_spider()"
```

## Output

- **SQLite**: `spiers/steel-statistics/data/statistical_data.db`
- **JSON**: `spiers/steel-statistics/output/statistical_data.json`

## Database Schema

```sql
CREATE TABLE statistical_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_date TEXT,
    region TEXT,
    indicator_type TEXT,
    current_value REAL,
    prev_value REAL,
    yoy_change REAL,
    mom_change REAL,
    unit TEXT,
    source_url TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

## Common Statistics Types

1. **粗钢产量** - Crude steel production (million tons)
2. **钢材产量** - Steel product output (million tons)
3. **产能利用率** - Capacity utilization rate (%)
4. **出口量** - Export volume (tons)
5. **进口量** - Import volume (tons)
6. **销售价格** - Sales price (yuan/ton)

## Notes

- Government sites may require proper headers to avoid CAPTCHA
- Data availability varies by month-end timing
- Some sources provide download links (Excel/PDF) instead of direct API
