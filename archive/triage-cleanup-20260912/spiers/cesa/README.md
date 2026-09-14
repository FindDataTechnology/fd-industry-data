# CESA Spider - 中国电子工业标准化技术协会数据爬虫

Specialized spider for extracting electronics industry statistics, technology standards, and market research from CESA.

## Data Sources

- **Primary Source**: http://www.cesa.cn/
- **Data Type**: Electronics industry statistics, technology standards, market research
- **Update Frequency**: Monthly
- **Focus**: IC output, software revenue, electronics production

## Features

- Extracts electronics industry statistics by sub-sector
- Scrapes technology standards information
- Collects industry news and policy documents
- Electronics category identification (IC, semiconductor, software, etc.)
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
from spiers.cesa.spider import get_cesa_data

results = get_cesa_data()
results = get_cesa_data(include_stats=True, include_news=False)
```

### Command Line

```bash
cd spiers/cesa
python spider.py
```

## Output

### SQLite Database (`data/cesa.db`)

**Table: `industry_statistics`**
- `period`, `category`, `category_cn`, `indicator_name`, `value`, `unit`
- `growth_rate`, `growth_unit`, `source_url`, `scraped_at`

**Table: `industry_news`**
- `date`, `title`, `content`, `category`, `source_url`, `scraped_at`

### JSON Exports

- `output/cesa_statistics.json`
- `output/cesa_news.json`

## Electronics Category Coverage

| Category | Chinese | Type |
|----------|---------|------|
| IC | 集成电路 | Core |
| Semiconductor | 半导体 | Core |
| Software | 软件 | Service |
| Electronic Info | 电子信息 | General |
| Telecom Equipment | 通信设备 | Equipment |
| Consumer Electronics | 消费电子 | Product |
| Components | 元器件 | Component |
| Display Panel | 显示面板 | Component |
| PCB | PCB | Component |
| LED | LED | Component |

## Authentication Requirements

- **No authentication required** for public statistics and news
- Some standards documents may require membership access

## Anti-Bot Measures

- Browser impersonation (chrome)
- Rate limiting (2s delay)
- Respectful crawling

## Known Limitations

1. Site structure may change, requiring selector updates
2. Statistics are updated monthly
3. Some standards documents require paid access

## Data Quality

- **Reliability**: High (official standards body)
- **Timeliness**: Monthly updates
- **Completeness**: Comprehensive electronics industry coverage
