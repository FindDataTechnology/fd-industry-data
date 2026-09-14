# CPIA Spider - 中国医药工业信息中心数据爬虫

Specialized spider for extracting pharmaceutical industry data from China Pharmaceutical Industry Association (CPIA).

## Data Sources

- **Primary Source**: http://www.cpia.org.cn/
- **Data Type**: Drug production statistics, sales data, trade data, industry news
- **Update Frequency**: Monthly
- **Coverage**: National pharmaceutical industry statistics

## Features

- Extracts drug production statistics (chemical drugs, TCM, biologics, API)
- Captures pharmaceutical sales data by channel
- Collects import/export trade data
- Covers 11+ pharmaceutical product categories
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
from spiers.cpia.spider import get_cpia_data

results = get_cpia_data()

results = get_cpia_data(
    categories=["production", "trade"],
    include_news=True
)
```

### Command Line

```bash
cd spiers/cpia
python spider.py
```

## Output

### SQLite Database (`data/cpia.db`)

| Table | Description |
|-------|-------------|
| `drug_production` | Drug production statistics |
| `drug_sales` | Pharmaceutical sales data by channel |
| `drug_trade` | Import/export trade statistics |
| `news_articles` | Industry news |

### JSON Exports

- `output/cpia_production.json`
- `output/cpia_sales.json`
- `output/cpia_trade.json`
- `output/cpia_news.json`

## Product Coverage

| Product | Chinese | Unit |
|---------|---------|------|
| Chemical Drugs | 化学药品 | 亿元 |
| Chinese Patent Medicine | 中成药 | 亿元 |
| Biological Products | 生物制品 | 亿元 |
| API | 原料药 | 万吨 |
| Antibiotics | 抗生素 | 万吨 |
| Vitamins | 维生素 | 万吨 |
| Injection | 注射剂 | 亿支 |
| Tablets | 片剂 | 亿片 |

## Authentication Requirements

**No authentication required.** All data is publicly accessible on the CPIA website.

## Known Limitations

1. **Site Availability**: cpia.org.cn may have intermittent availability
2. **Premium Data**: Some detailed statistics may require membership
3. **Rate Limits**: Aggressive scraping may trigger blocks

## Data Quality

- **Reliability**: High (official industry information center)
- **Timeliness**: Monthly updates
- **Completeness**: Comprehensive national coverage
- **Accuracy**: Official statistics
