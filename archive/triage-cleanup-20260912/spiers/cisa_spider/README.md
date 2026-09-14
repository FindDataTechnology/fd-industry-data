# China Iron & Steel Association Spider (中国钢铁工业协会)

Extracts production statistics, capacity utilization rates, and market data from China's primary steel industry association.

## Data Source

| Field | Value |
|-------|-------|
| **Source** | 中国钢铁工业协会 (CISA) |
| **URL** | https://www.cisa.org.cn |
| **Score** | 98/100 |
| **Category** | Industry Production Statistics |

### Description

The China Iron & Steel Association (CISA) is the official industry organization representing China's steel manufacturing sector. This spider extracts:

- **Production Statistics**: Crude steel output, castings, rolled products
- **Capacity Utilization**: Industry-wide utilization rates
- **Trade Data**: Import/export volumes by product type
- **Price Indices**: Domestic and international steel price benchmarks
- **Market Reports**: Monthly analysis and forecasts

## Usage

```bash
cd /Users/chengsishi/finddata/fd-industry-data

# Run spider (all configured URLs)
uv run python spiers/cisa_spider.py

# View results
cat spiers/cisa_spider/output/production_statistics.json
```

## Output

### SQLite Database

**Location**: `spiers/cisa_spider/data/cisa_production.db`

**Schema**:

```sql
CREATE TABLE production_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT,                -- Report period (e.g., "2024-01")
    indicator_name TEXT,             -- Indicator name (e.g., "crude_steel_output")
    value REAL,                      -- Numeric value
    unit TEXT,                       -- Unit (e.g., "万吨", "%")
    change_rate TEXT,                -- Month-over-month or year-over-year change
    source_url TEXT,                 -- Original page URL
    page_category TEXT,              -- Category classification
    raw_data TEXT,                   -- JSON backup of row data
    fetched_at TEXT NOT NULL         -- Timestamp when fetched
);
```

### JSON Export

**Location**: `spiers/cisa_spider/output/production_statistics.json`

Format: Array of record objects matching the database schema.

Example record:

```json
{
  "report_date": "2024-01",
  "indicator_name": "crude_steel_output",
  "value": 9500.0,
  "unit": "万吨",
  "change_rate": "+2.3%",
  "source_url": "https://www.cisa.org.cn/ssjg/index.html",
  "raw_data": "{\"headers\": [...], \"row\": [...]}"
}
```

## Manifest Configuration

Create `manifests/cisa-production.yaml`:

```yaml
name: cisa-production
label: 中国钢铁协会 - 生产统计数据
source_url: https://www.cisa.org.cn
functions:
  - command: get_production_stats
    description: 获取钢铁生产统计数据
    category: industry-production
    frequency: monthly
    columns:
      - name: report_date
        type: str
        description: 统计报告期
      - name: indicator_name
        type: str
        description: 指标名称
      - name: value
        type: float
        description: 指标数值
      - name: unit
        type: str
        description: 单位
      - name: change_rate
        type: str
        description: 变化情况
```

## Notes

- Respects rate limiting with 2-second delays between requests
- Automatic retries for transient failures
- User agent rotation to avoid detection
- All data includes source attribution
- Schema supports duplicate prevention via UNIQUE constraint

## References

- [China Iron & Steel Association](https://www.cisa.org.cn)
- Industry Classification: Black Metals → Steel Manufacturing
