# China Nuclear Energy Association Spider - 中国核能行业协会数据爬虫

Specialized spider for extracting nuclear energy data from China Nuclear Energy Association.

## Data Sources

- **Primary Source**: http://www.china-nea.org/
- **Data Type**: Nuclear power statistics, plant operations, safety records
- **Update Frequency**: Monthly
- **Coverage**: All operating nuclear power plants in China

## Features

- Extracts national nuclear power generation statistics
- Captures individual plant operation data (15 plants)
- Collects WANO safety performance indices
- Tracks construction progress of new units
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
from spiers.china_nea.spider import get_china_nea_data

results = get_china_nea_data()

results = get_china_nea_data(
    categories=["plants", "safety"],
    include_news=True
)
```

### Command Line

```bash
cd spiers/china-nea
python spider.py
```

## Output

### SQLite Database (`data/china_nea.db`)

| Table | Description |
|-------|-------------|
| `power_stats` | National nuclear power generation statistics |
| `plant_operation` | Individual plant operation data |
| `safety_records` | WANO safety indices and incident records |
| `news_articles` | Industry news |

### JSON Exports

- `output/china_nea_power.json`
- `output/china_nea_plants.json`
- `output/china_nea_safety.json`
- `output/china_nea_news.json`

## Nuclear Plant Coverage

| Plant | Chinese | Location |
|-------|---------|----------|
| Daya Bay | 大亚湾核电站 | 广东深圳 |
| Lingao | 岭澳核电站 | 广东深圳 |
| Qinshan | 秦山核电站 | 浙江海盐 |
| Tianwan | 田湾核电站 | 江苏连云港 |
| Hongyanhe | 红沿河核电站 | 辽宁大连 |
| Ningde | 宁德核电站 | 福建宁德 |
| Yangjiang | 阳江核电站 | 广东阳江 |
| Taishan | 台山核电站 | 广东台山 |
| Fuqing | 福清核电站 | 福建福清 |
| Fangchenggang | 防城港核电站 | 广西防城港 |
| Changjiang | 昌江核电站 | 海南昌江 |
| Sanmen | 三门核电站 | 浙江三门 |
| Haiyang | 海阳核电站 | 山东海阳 |
| Shidaowan | 荣成石岛湾核电站 | 山东荣成 |
| Xiapu | 霞浦核电站 | 福建霞浦 |

## Safety Metrics

| Metric | Description |
|--------|-------------|
| WANO Score | World Association of Nuclear Operators composite index |
| Safety Level | INES safety classification |
| Incidents | Number of reported safety events |

## Authentication Requirements

**No authentication required.** All data is publicly accessible.

## Known Limitations

1. **Site Availability**: china-nea.org may have intermittent availability
2. **Data Granularity**: Real-time plant status may require special access
3. **Rate Limits**: Aggressive scraping may trigger blocks

## Data Quality

- **Reliability**: High (official industry association)
- **Timeliness**: Monthly updates
- **Completeness**: All operating plants covered
- **Accuracy**: Official statistics with WANO validation

## See Also

- [CEC Spider](../cec/README.md) - Electricity industry data
- [CPCIA Spider](../cpcia/README.md) - Petroleum and chemical data
