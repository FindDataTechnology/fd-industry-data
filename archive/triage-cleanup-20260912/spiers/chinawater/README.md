# China Water Resources Association Spider - 中国水利学会数据爬虫

Specialized spider for extracting water resources data from China Water Resources Association.

## Data Sources

- **Primary Source**: http://www.chinawater.com.cn/
- **Data Type**: Water resources, irrigation, water quality, infrastructure
- **Update Frequency**: Monthly
- **Coverage**: National water resources and infrastructure data

## Features

- Extracts water resource statistics (supply, demand, per capita)
- Captures irrigation and farmland data
- Collects water quality monitoring data (pH, DO, COD, ammonia)
- Tracks infrastructure data (reservoirs, dams, canals)
- River basin-level data support
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
from spiers.chinawater.spider import get_chinawater_data

results = get_chinawater_data()

results = get_chinawater_data(
    categories=["resources", "quality"],
    include_news=True
)
```

### Command Line

```bash
cd spiers/chinawater
python spider.py
```

## Output

### SQLite Database (`data/chinawater.db`)

| Table | Description |
|-------|-------------|
| `water_resources` | Water supply and consumption statistics |
| `irrigation_stats` | Irrigation and farmland data |
| `water_quality` | Water quality monitoring data |
| `infrastructure` | Reservoirs, dams, and other facilities |
| `news_articles` | Industry news |

### JSON Exports

- `output/chinawater_resources.json`
- `output/chinawater_irrigation.json`
- `output/chinawater_quality.json`
- `output/chinawater_infrastructure.json`
- `output/chinawater_news.json`

## Water Quality Parameters

| Parameter | Description | Unit |
|-----------|-------------|------|
| pH | Acidity/alkalinity | - |
| Dissolved Oxygen | DO content | mg/L |
| COD | Chemical oxygen demand | mg/L |
| Ammonia Nitrogen | NH3-N content | mg/L |
| Quality Class | I-V classification | - |

## Infrastructure Types

| Type | Chinese | Description |
|------|---------|-------------|
| Reservoir | 水库 | Water storage |
| Dam | 大坝 | Water barrier |
| Sluice | 闸 | Flow control |
| Pump Station | 泵站 | Water pumping |
| Canal | 渠道 | Water conveyance |
| Levee | 堤防 | Flood protection |

## Authentication Requirements

**No authentication required.** All data is publicly accessible.

## Known Limitations

1. **Site Availability**: chinawater.com.cn may have intermittent availability
2. **Data Granularity**: Real-time monitoring data may require special access
3. **Rate Limits**: Aggressive scraping may trigger blocks

## Data Quality

- **Reliability**: High (official industry association)
- **Timeliness**: Monthly/quarterly updates
- **Completeness**: National coverage with river basin detail
- **Accuracy**: Official statistics

## See Also

- [CAEP Spider](../caep/README.md) - Environmental protection data
