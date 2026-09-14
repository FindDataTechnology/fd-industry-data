# China Electricity Council Spider - 中国电力企业联合会数据爬虫

Specialized spider for extracting electricity industry data from China Electricity Council (CEC).

## Data Sources

- **Primary Source**: http://www.cec.org.cn/
- **Data Type**: Electricity production, consumption, grid infrastructure, renewable energy
- **Update Frequency**: Monthly
- **Coverage**: National and regional electricity statistics

## Features

- Extracts electricity production and consumption statistics
- Captures grid infrastructure data
- Collects renewable energy statistics (wind, solar, hydro, nuclear)
- Regional data support
- Rate limiting (2s delay)
- SQLite storage + JSON export

## Installation

```bash
cd fd-industry-data
uv sync
```

## Usage

### Python API

```python
from spiers.cec.spider import get_cec_data

results = get_cec_data()

results = get_cec_data(
    categories=["production", "renewable"],
    include_news=True
)

stats = results["stats"]
news = results["news"]
renewable = results["renewable"]
```

### Command Line

```bash
cd spiers/cec
python spider.py
```

## Output

### SQLite Database (`data/cec.db`)

| Table | Description |
|-------|-------------|
| `electricity_stats` | Production and consumption statistics |
| `renewable_stats` | Renewable energy capacity and generation |
| `news_articles` | Industry news and announcements |

### JSON Exports

- `output/cec_electricity_stats.json`
- `output/cec_renewable.json`
- `output/cec_news.json`

## Authentication Requirements

**No authentication required.** All data is publicly accessible on the CEC website.

## Known Limitations

1. **Site Availability**: cec.org.cn may have intermittent availability
2. **Content Structure**: HTML structure may change, requiring selector updates
3. **Data Granularity**: Some detailed statistics may require membership access
4. **Rate Limits**: Aggressive scraping may trigger blocks

## Data Quality

- **Reliability**: High (official industry association)
- **Timeliness**: Monthly updates
- **Completeness**: Comprehensive national coverage
- **Accuracy**: Official statistics

## See Also

- [CPCIA Spider](../cpcia/README.md) - Petroleum and chemical industry data
- [China NEA Spider](../china-nea/README.md) - Nuclear energy data
