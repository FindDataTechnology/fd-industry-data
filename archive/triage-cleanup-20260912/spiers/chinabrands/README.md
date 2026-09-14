# China Brands Spider - 中国品牌促进会数据爬虫

Specialized spider for extracting brand value data, brand rankings, and market share information from China Brand Association.

## Data Sources

- **Primary Source**: http://www.chinabrands.org/
- **Data Type**: Brand value data, brand rankings, market share, consumer preference
- **Update Frequency**: Yearly
- **Focus**: Brand valuation, top 500 brands, industry brand rankings, consumer brand preference

## Features

- Extracts brand rankings with scores and industry classification
- Brand valuation data with year-over-year changes
- Market share data by brand and industry
- Industry identification (technology, finance, consumer goods, etc.)
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
from spiers.chinabrands.spider import get_chinabrands_data

results = get_chinabrands_data()
results = get_chinabrands_data(include_rankings=True, include_valuation=True, include_market_share=False, include_news=False)
```

### Command Line

```bash
cd spiers/chinabrands
python spider.py
```

## Output

### SQLite Database (`data/chinabrands.db`)

**Table: `brand_rankings`**
- `year`, `rank`, `brand_name`, `brand_name_cn`, `industry`, `industry_cn`
- `score`, `source_url`, `scraped_at`

**Table: `brand_valuation`**
- `year`, `brand_name`, `brand_name_cn`, `valuation`, `valuation_unit`
- `industry`, `industry_cn`, `yoy_change`, `source_url`, `scraped_at`

**Table: `market_share`**
- `period`, `brand_name`, `brand_name_cn`, `industry`, `market_share`
- `market_share_unit`, `revenue`, `revenue_unit`, `source_url`, `scraped_at`

**Table: `industry_news`**
- `date`, `title`, `content`, `category`, `source_url`, `scraped_at`

### JSON Exports

- `output/chinabrands_rankings.json`
- `output/chinabrands_valuation.json`
- `output/chinabrands_market_share.json`
- `output/chinabrands_news.json`

## Brand Industry Coverage

| Industry | Chinese | Type |
|----------|---------|------|
| Technology | 科技 | Tech |
| Internet | 互联网 | Internet |
| Finance | 金融 | Financial |
| Consumer Goods | 消费品 | Consumer |
| Food & Beverage | 食品 | Food |
| Apparel | 服装 | Fashion |
| Electronics | 家电 | Electronics |
| Automobile | 汽车 | Automotive |
| Pharmaceutical | 医药 | Healthcare |
| Real Estate | 房地产 | Property |
| Education | 教育 | Education |
| Media | 传媒 | Media |
| Energy | 能源 | Energy |
| Agriculture | 农业 | Agriculture |

## Authentication Requirements

- **No authentication required** for public brand rankings and news
- Brand valuation data is publicly available
- Market share reports are published openly

## Anti-Bot Measures

- Browser impersonation (chrome)
- Rate limiting (2s delay between requests)
- Respectful crawling with proper User-Agent

## Known Limitations

1. Site structure may change, requiring selector updates
2. Brand rankings are typically updated yearly
3. Valuation methodology may vary between sources
4. Market share data availability varies by industry

## Data Quality

- **Reliability**: High (official brand promotion association)
- **Timeliness**: Yearly major updates
- **Completeness**: Comprehensive brand and market coverage
