# CISA Production Statistics Spider (中国钢铁工业协会)

Extracts industry production statistics and capacity utilization data from China Iron & Steel Association.

## Target Source

| Field | Value |
|-------|-------|
| **Name** | 中国钢铁工业协会 (CISA) |
| **URL** | https://www.cisa.org.cn |
| **Score** | 98/100 |
| **Category** | Industry Statistics |

## Data Extracted

- **Production Statistics**: Monthly/yearly steel production volumes
- **Capacity Utilization**: Factory capacity utilization rates
- **Import/Export Data**: Steel trade statistics
- **Price Indices**: Market price indicators
- **Industry Analysis**: Market reports and forecasts

## Usage

```bash
cd /Users/chengsishi/finddata/fd-industry-data

# Run spider with all configured URLs
uv run python spiers/cisa_spider.py

# View results
cat spiers/cisa/output/production_statistics.json
```

## Output Files

### SQLite Database
- **Path**: `spiers/cisa/data/cisa_production.db`
- **Table**: `production_stats`

### Schema
```sql
CREATE TABLE production_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT,              -- Statistical date (YYYY-MM or YYYY年 MM 月)
    indicator_name TEXT,           -- Name of indicator (e.g., "粗钢产量", "产能利用率")
    value REAL,                    -- Numeric value
    unit TEXT,                     -- Unit (万吨，%, etc.)
    change_rate TEXT,              -- Month-over-month or year-over-year change
    source_url TEXT,               -- Source URL
    page_category TEXT,            -- Category of source page
    raw_data TEXT,                 -- JSON serialized original data
    fetched_at TEXT NOT NULL       -- ISO timestamp when fetched
);
```

### JSON Export
- **Path**: `spiers/cisa/output/production_statistics.json`
- **Format**: Array of extracted records with standardized fields

## Database Schema Details

| Column | Type | Description |
|--------|------|-------------|
| `report_date` | TEXT | Report period (e.g., "2024-01", "2024 年 1 月") |
| `indicator_name` | TEXT | Indicator name in Chinese |
| `value` | REAL | Numeric measurement |
| `unit` | TEXT | Unit of measure |
| `change_rate` | TEXT | Change percentage (e.g., "+5.2%", "-1.3%") |
| `source_url` | TEXT | Original webpage URL |
| `page_category` | TEXT | Classification of data source |
| `raw_data` | TEXT | Full raw record as JSON |
| `fetched_at` | TEXT | ISO timestamp |

## API Functions

The data can be accessed via MCP functions:

- `get_production_stats(date_range?, indicator?)` - Query production statistics
- `get_capacity_utilization(year?)` - Get capacity utilization rates
- `get_export_import_data(month?)` - Trade statistics

## Notes

- Data typically updated monthly on the association website
- Some pages may require authentication or have JavaScript rendering
- User-agent rotation implemented to avoid blocking
- Rate limiting: 2-second delays between requests
- Maximum retries: Built-in error recovery

## Related Projects

- [fd-industry-data](https://github.com/FindDataOfficial/fd-industry-data) - Main repository
- [Scrapling](https://github.com/TeamShiksha/scrapling) - Scraping framework
