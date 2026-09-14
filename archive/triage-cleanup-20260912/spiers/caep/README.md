# CAEP Spider - 中国环境保护产业协会数据爬虫

Specialized spider for extracting environmental industry data from China Association of Environmental Protection Industry (CAEP).

## Data Sources

- **Primary Source**: http://www.caep.org.cn/
- **Data Type**: Environmental industry statistics, pollution control, green technology
- **Update Frequency**: Monthly
- **Coverage**: National environmental protection industry data

## Features

- Extracts environmental industry revenue and employment data
- Captures pollution control statistics (wastewater, exhaust, solid waste)
- Collects green technology certification data
- Sub-sector analysis (water, air, waste, soil, noise)
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
from spiers.caep.spider import get_caep_data

results = get_caep_data()

results = get_caep_data(
    categories=["pollution", "technology"],
    include_news=True
)
```

### Command Line

```bash
cd spiers/caep
python spider.py
```

## Output

### SQLite Database (`data/caep.db`)

| Table | Description |
|-------|-------------|
| `industry_stats` | Environmental industry statistics |
| `pollution_control` | Pollution emission and treatment data |
| `green_technology` | Green technology certifications |
| `news_articles` | Industry news and policy updates |

### JSON Exports

- `output/caep_industry.json`
- `output/caep_pollution.json`
- `output/caep_technology.json`
- `output/caep_news.json`

## Pollutant Coverage

| Pollutant | Chinese | Unit |
|-----------|---------|------|
| Wastewater | 废水 | 万吨 |
| Exhaust Gas | 废气 | 亿标立方米 |
| Solid Waste | 固体废物 | 万吨 |
| SO2 | 二氧化硫 | 万吨 |
| NOx | 氮氧化物 | 万吨 |
| COD | 化学需氧量 | 万吨 |
| Ammonia Nitrogen | 氨氮 | 万吨 |
| PM2.5 | PM2.5 | 微克/立方米 |

## Sub-Sectors

| Sector | Chinese |
|--------|---------|
| Water Treatment | 水处理 |
| Air Pollution Control | 大气治理 |
| Solid Waste | 固体废物 |
| Noise Control | 噪声治理 |
| Soil Remediation | 土壤修复 |

## Authentication Requirements

**No authentication required.** All data is publicly accessible.

## Known Limitations

1. **Site Availability**: caep.org.cn may have intermittent availability
2. **Technology Database**: Full certification catalog may require membership
3. **Rate Limits**: Aggressive scraping may trigger blocks

## Data Quality

- **Reliability**: High (official industry association)
- **Timeliness**: Monthly updates
- **Completeness**: Comprehensive industry coverage
- **Accuracy**: Official statistics

## See Also

- [China Water Spider](../chinawater/README.md) - Water resources data
- [CEC Spider](../cec/README.md) - Electricity industry data
