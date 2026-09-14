# Tourism Spider - 中国旅游研究院数据爬虫

Specialized spider for extracting tourism statistics, visitor numbers, and revenue data from China Tourism Academy.

## Data Sources

- **Primary Source**: http://www.ctaweb.org.cn/
- **Data Type**: Tourism statistics, visitor numbers, revenue data, market analysis
- **Update Frequency**: Quarterly
- **Focus**: Domestic/inbound/outbound tourism, holiday travel data

## Features

- Extracts tourism statistics (visitor counts, revenue)
- Tourism type identification (domestic, inbound, outbound, etc.)
- Collects industry news and analysis reports
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
from spiers.tourism.spider import get_tourism_data

results = get_tourism_data()
results = get_tourism_data(include_stats=True, include_news=False)
```

### Command Line

```bash
cd spiers/tourism
python spider.py
```

## Output

### SQLite Database (`data/tourism.db`)

**Table: `tourism_statistics`**
- `period`, `tourism_type`, `tourism_type_cn`, `indicator_name`, `value`, `unit`
- `growth_rate`, `growth_unit`, `region`, `source_url`, `scraped_at`

**Table: `industry_news`**
- `date`, `title`, `content`, `category`, `source_url`, `scraped_at`

### JSON Exports

- `output/tourism_statistics.json`
- `output/tourism_news.json`

## Tourism Type Coverage

| Type | Chinese | Category |
|------|---------|----------|
| Domestic | 国内旅游 | Core |
| Inbound | 入境旅游 | Core |
| Outbound | 出境旅游 | Core |
| Red Tourism | 红色旅游 | Thematic |
| Rural | 乡村旅游 | Thematic |
| Ice & Snow | 冰雪旅游 | Thematic |
| Holiday | 假日旅游 | Seasonal |
| Cultural | 文化旅游 | Thematic |

## Authentication Requirements

- **No authentication required** for public statistics and news
- Some detailed reports may require institutional access

## Anti-Bot Measures

- Browser impersonation (chrome)
- Rate limiting (2s delay)
- Respectful crawling

## Known Limitations

1. Site structure may change, requiring selector updates
2. Statistics are updated quarterly
3. Some detailed reports require institutional access

## Data Quality

- **Reliability**: High (official research academy)
- **Timeliness**: Quarterly updates
- **Completeness**: Comprehensive tourism industry coverage
