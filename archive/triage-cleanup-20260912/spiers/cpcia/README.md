# CPCIA Spider - 中国石油和化学工业联合会数据爬虫

Specialized spider for extracting petrochemical industry data from China Petroleum and Chemical Industry Federation (CPCIA).

## Data Sources

- **Primary Source**: http://www.cpcia.org.cn/
- **Data Type**: Oil/gas production, refinery statistics, chemical industry data, trade data
- **Update Frequency**: Monthly
- **Coverage**: National petrochemical industry statistics

## Features

- Extracts oil and gas production statistics
- Captures chemical product output data
- Collects import/export trade data
- Covers 13+ petrochemical products
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
from spiers.cpcia.spider import get_cpcia_data

results = get_cpcia_data()

results = get_cpcia_data(
    categories=["oilgas", "trade"],
    include_news=True
)
```

### Command Line

```bash
cd spiers/cpcia
python spider.py
```

## Output

### SQLite Database (`data/cpcia.db`)

| Table | Description |
|-------|-------------|
| `oilgas_stats` | Oil and gas production statistics |
| `chemical_stats` | Chemical industry production data |
| `trade_stats` | Import/export trade statistics |
| `news_articles` | Industry news |

### JSON Exports

- `output/cpcia_oilgas.json`
- `output/cpcia_chemical.json`
- `output/cpcia_trade.json`
- `output/cpcia_news.json`

## Product Coverage

| Product | Chinese | Unit |
|---------|---------|------|
| Crude Oil | 原油 | 万吨 |
| Natural Gas | 天然气 | 亿立方米 |
| Gasoline | 汽油 | 万吨 |
| Diesel | 柴油 | 万吨 |
| Ethylene | 乙烯 | 万吨 |
| Methanol | 甲醇 | 万吨 |
| Chemical Fiber | 化学纤维 | 万吨 |
| Fertilizer | 化肥 | 万吨 |

## Authentication Requirements

**No authentication required.** All data is publicly accessible.

## Known Limitations

1. **Site Availability**: cpcia.org.cn may have intermittent availability
2. **Premium Data**: Some detailed statistics may require membership
3. **Rate Limits**: Aggressive scraping may trigger blocks

## Data Quality

- **Reliability**: High (official industry federation)
- **Timeliness**: Monthly updates
- **Completeness**: Comprehensive national coverage
- **Accuracy**: Official statistics

## See Also

- [CEC Spider](../cec/README.md) - Electricity industry data
- [CCTDA Spider](../cctda/README.md) - Coal industry data
