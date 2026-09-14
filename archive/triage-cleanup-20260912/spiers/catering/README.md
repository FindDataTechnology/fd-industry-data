# Catering Spider - 中国餐饮行业协会数据爬虫

Specialized spider for extracting restaurant industry data, food service statistics, and consumer behavior from China Catering Industry Association.

## Data Sources

- **Primary Source**: http://www.chinaca.org/
- **Data Type**: Catering revenue, restaurant statistics, market trends, consumer behavior
- **Update Frequency**: Monthly
- **Focus**: Restaurant revenue, store counts, delivery data, consumer spending

## Features

- Extracts catering revenue and market statistics
- Restaurant category identification (full-service, fast food, hotpot, etc.)
- Consumer behavior and spending data
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
from spiers.catering.spider import get_catering_data

results = get_catering_data()
results = get_catering_data(include_stats=True, include_news=False)
```

### Command Line

```bash
cd spiers/catering
python spider.py
```

## Output

### SQLite Database (`data/catering.db`)

**Table: `catering_statistics`**
- `period`, `category`, `category_cn`, `indicator_name`, `value`, `unit`
- `growth_rate`, `growth_unit`, `region`, `source_url`, `scraped_at`

**Table: `industry_news`**
- `date`, `title`, `content`, `category`, `source_url`, `scraped_at`

### JSON Exports

- `output/catering_statistics.json`
- `output/catering_news.json`

## Catering Category Coverage

| Category | Chinese | Type |
|----------|---------|------|
| Full Service | 正餐 | Traditional |
| Fast Food | 快餐 | Quick service |
| Hotpot | 火锅 | Specialty |
| Barbecue | 烧烤 | Specialty |
| Beverages | 饮品 | Drinks |
| Bakery | 烘焙 | Baked goods |
| Group Catering | 团餐 | Institutional |
| Delivery | 外卖 | Online |
| Prepared Food | 预制菜 | Pre-made |

## Authentication Requirements

- **No authentication required** for public statistics and news
- Some detailed industry reports may require membership

## Anti-Bot Measures

- Browser impersonation (chrome)
- Rate limiting (2s delay)
- Respectful crawling

## Known Limitations

1. Site structure may change, requiring selector updates
2. Statistics are updated monthly
3. Some detailed reports require paid membership
4. Regional granularity varies

## Data Quality

- **Reliability**: High (official industry association)
- **Timeliness**: Monthly updates
- **Completeness**: Comprehensive catering industry coverage
