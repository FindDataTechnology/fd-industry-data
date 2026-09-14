# CGCC Spider - 中国商业联合会数据爬虫

Specialized spider for extracting commercial industry data, trade statistics, and market analysis from China National Commercial Association.

## Data Sources

- **Primary Source**: http://www.cgcc.org.cn/
- **Data Type**: Commercial industry data, trade statistics, market analysis
- **Update Frequency**: Monthly
- **Focus**: Retail sales, wholesale trade, commercial index, consumer goods market

## Features

- Extracts trade statistics (retail sales, wholesale trade)
- Commercial index data (business climate, confidence indices)
- Market analysis reports with key findings extraction
- Commercial category identification (wholesale, retail, logistics, etc.)
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
from spiers.cgcc.spider import get_cgcc_data

results = get_cgcc_data()
results = get_cgcc_data(include_trade=True, include_index=True, include_analysis=False, include_news=False)
```

### Command Line

```bash
cd spiers/cgcc
python spider.py
```

## Output

### SQLite Database (`data/cgcc.db`)

**Table: `trade_statistics`**
- `period`, `category`, `category_cn`, `indicator_name`, `value`, `unit`
- `growth_rate`, `growth_unit`, `region`, `source_url`, `scraped_at`

**Table: `commercial_index`**
- `period`, `index_name`, `index_value`, `change_value`, `sub_index`
- `source_url`, `scraped_at`

**Table: `market_analysis`**
- `date`, `title`, `summary`, `category`, `key_findings`
- `source_url`, `scraped_at`

**Table: `industry_news`**
- `date`, `title`, `content`, `category`, `source_url`, `scraped_at`

### JSON Exports

- `output/cgcc_trade_statistics.json`
- `output/cgcc_commercial_index.json`
- `output/cgcc_market_analysis.json`
- `output/cgcc_news.json`

## Commercial Category Coverage

| Category | Chinese | Type |
|----------|---------|------|
| Wholesale | 批发 | Trade |
| Retail | 零售 | Trade |
| Accommodation | 住宿 | Hospitality |
| Catering | 餐饮 | Food Service |
| Logistics | 物流 | Supply Chain |
| Supply Chain | 供应链 | Operations |
| Consumer Goods | 消费品 | Products |
| Producer Goods | 生产资料 | Industrial |
| Import/Export | 进出口 | International Trade |

## Authentication Requirements

- **No authentication required** for public trade data and news
- Commercial index data is publicly available
- Market analysis reports are published openly

## Anti-Bot Measures

- Browser impersonation (chrome)
- Rate limiting (2s delay between requests)
- Respectful crawling with proper User-Agent

## Known Limitations

1. Site structure may change, requiring selector updates
2. Trade statistics are updated monthly
3. Commercial index methodology may vary
4. Regional data availability is limited

## Data Quality

- **Reliability**: High (official commercial association)
- **Timeliness**: Monthly updates
- **Completeness**: Comprehensive commercial industry coverage
