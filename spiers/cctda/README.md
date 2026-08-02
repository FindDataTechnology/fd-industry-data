# CCTDA Spider - 中国煤炭运销协会数据爬虫

Specialized spider for extracting coal industry data from China Coal Transport and Distribution Association (CCTDA).

## Data Sources

- **Primary Source**: http://www.cctda.com.cn/
- **Data Type**: Coal production, transportation, price indices, market analysis
- **Update Frequency**: Weekly
- **Coverage**: National coal market data

## Features

- Extracts coal production statistics by type
- Captures transportation volume data (railway, port, road, waterway)
- Collects coal price indices and spot prices
- Covers major coal routes and ports
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
from spiers.cctda.spider import get_cctda_data

results = get_cctda_data()

results = get_cctda_data(
    categories=["price", "transport"],
    include_news=True
)
```

### Command Line

```bash
cd spiers/cctda
python spider.py
```

## Output

### SQLite Database (`data/cctda.db`)

| Table | Description |
|-------|-------------|
| `production_stats` | Coal production by type and region |
| `transport_stats` | Transportation volume by mode and route |
| `price_index` | Coal price indices and spot prices |
| `news_articles` | Market analysis and news |

### JSON Exports

- `output/cctda_production.json`
- `output/cctda_transport.json`
- `output/cctda_price.json`
- `output/cctda_news.json`

## Coal Type Coverage

| Type | Chinese | Unit |
|------|---------|------|
| Thermal Coal | 动力煤 | 元/吨 |
| Coking Coal | 焦煤 | 元/吨 |
| Anthracite | 无烟煤 | 元/吨 |
| Lignite | 褐煤 | 元/吨 |
| Raw Coal | 原煤 | 万吨 |

## Transport Routes

- 大秦线 (Datong-Qinhuangdao Railway)
- 朔黄线 (Shuozhou-Huanghua Railway)
- 秦皇岛港 (Qinhuangdao Port)
- 唐山港 (Tangshan Port)
- 黄骅港 (Huanghua Port)
- 环渤海 (Bohai Rim)

## Authentication Requirements

**No authentication required.** All data is publicly accessible.

## Known Limitations

1. **Site Availability**: cctda.com.cn may have intermittent availability
2. **Price Data**: Real-time prices may require subscription
3. **Rate Limits**: Aggressive scraping may trigger blocks

## Data Quality

- **Reliability**: High (official industry association)
- **Timeliness**: Weekly updates for price data
- **Completeness**: Comprehensive coal market coverage
- **Accuracy**: Official market data

## See Also

- [CEC Spider](../cec/README.md) - Electricity industry data
- [CPCIA Spider](../cpcia/README.md) - Petroleum and chemical data
