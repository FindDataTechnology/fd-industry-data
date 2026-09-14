# CAMDI Spider - 中国医疗器械行业协会数据爬虫

Specialized spider for extracting medical device industry data from China Association of Medical Device Industry (CAMDI).

## Data Sources

- **Primary Source**: http://www.camdi.org/
- **Data Type**: Medical device production, market statistics, regulatory info, technology trends
- **Update Frequency**: Monthly
- **Coverage**: National medical device industry

## Features

- Extracts medical device production statistics
- Captures market size and growth data
- Collects registration and approval information
- Covers 11+ device categories (imaging, IVD, consumables, etc.)
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
from spiers.camdi.spider import get_camdi_data

results = get_camdi_data()

results = get_camdi_data(
    categories=["production", "market"],
    include_news=True
)
```

### Command Line

```bash
cd spiers/camdi
python spider.py
```

## Output

### SQLite Database (`data/camdi.db`)

| Table | Description |
|-------|-------------|
| `device_production` | Medical device production statistics |
| `market_stats` | Market size, growth, and trade data |
| `regulatory_info` | Registration and approval information |
| `news_articles` | Industry news |

### JSON Exports

- `output/camdi_production.json`
- `output/camdi_market.json`
- `output/camdi_regulatory.json`
- `output/camdi_news.json`

## Device Category Coverage

| Category | Chinese | Unit |
|----------|---------|------|
| Medical Imaging | 医学影像设备 | 台 |
| IVD | 体外诊断 | 亿元 |
| High-value Consumables | 高值耗材 | 亿元 |
| Low-value Consumables | 低值耗材 | 亿元 |
| Surgical Equipment | 手术设备 | 台 |
| Dental Equipment | 口腔设备 | 台 |
| Rehabilitation | 康复器械 | 台 |
| Home Medical Devices | 家用医疗器械 | 亿元 |
| Medical AI | 医疗AI | 亿元 |

## Authentication Requirements

**No authentication required.** All data is publicly accessible on the CAMDI website.

## Known Limitations

1. **Site Availability**: camdi.org may have intermittent availability
2. **Registration Data**: Detailed registration info may require NMPA database access
3. **Rate Limits**: Aggressive scraping may trigger blocks

## Data Quality

- **Reliability**: High (official industry association)
- **Timeliness**: Monthly updates
- **Completeness**: Comprehensive national coverage
- **Accuracy**: Official statistics
