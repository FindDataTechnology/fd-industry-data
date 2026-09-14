# CNTAC Spider - 中国纺织工业联合会数据爬虫

Specialized spider for extracting textile production data, export statistics, and industry reports from China Textile Industry Federation.

## Data Sources

- **Primary Source**: http://www.cntac.org.cn/
- **Data Type**: Production statistics, export data, market analysis, industry reports
- **Update Frequency**: Monthly
- **Focus**: Textile output, yarn/fabric production, export volumes

## Features

- Extracts textile production statistics by category
- Scrapes export volume and value data
- Collects industry news and policy documents
- Textile category identification (yarn, fabric, cotton, etc.)
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
from spiers.cntac.spider import get_cntac_data

results = get_cntac_data()
results = get_cntac_data(include_production=True, include_export=True, include_news=False)
```

### Command Line

```bash
cd spiers/cntac
python spider.py
```

## Output

### SQLite Database (`data/cntac.db`)

**Table: `production_statistics`**
- `period`, `category`, `category_cn`, `production_volume`, `production_unit`
- `growth_rate`, `growth_unit`, `region`, `source_url`, `scraped_at`

**Table: `export_statistics`**
- `period`, `category`, `category_cn`, `export_volume`, `export_unit`
- `export_value`, `value_unit`, `growth_rate`, `source_url`, `scraped_at`

**Table: `industry_news`**
- `date`, `title`, `content`, `category`, `source_url`, `scraped_at`

### JSON Exports

- `output/cntac_production.json`
- `output/cntac_export.json`
- `output/cntac_news.json`

## Textile Category Coverage

| Category | Chinese | Type |
|----------|---------|------|
| Yarn | 纱 | Raw material |
| Fabric | 布 | Material |
| Cotton | 棉花 | Raw material |
| Chemical Fiber | 化纤 | Raw material |
| Silk | 丝绸 | Material |
| Hemp | 麻 | Raw material |
| Garment | 服装 | Finished product |
| Home Textile | 家纺 | Finished product |
| Industrial Textile | 产业用纺织品 | Industrial |

## Authentication Requirements

- **No authentication required** for public statistics and news
- Some detailed reports may require CNTAC membership

## Anti-Bot Measures

- Browser impersonation (chrome)
- Rate limiting (2s delay)
- Respectful crawling

## Known Limitations

1. Site structure may change, requiring selector updates
2. Statistics are updated monthly
3. Some premium reports require membership

## Data Quality

- **Reliability**: High (official industry federation)
- **Timeliness**: Monthly updates
- **Completeness**: Comprehensive textile industry coverage
