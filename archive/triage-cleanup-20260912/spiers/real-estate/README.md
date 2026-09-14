# Real Estate Spider - 中国房地产业协会数据爬虫

Specialized spider for extracting real estate market data, property prices, and construction statistics from China Real Estate Association.

## Data Sources

- **Primary Source**: http://www.fangchan.com/
- **Data Type**: Property prices, market transactions, development investment, construction statistics
- **Update Frequency**: Monthly
- **Focus**: Housing prices by city, transaction volumes, land data

## Features

- Extracts property price indices by city and tier
- Scrapes market transaction data (area, volume, value)
- Collects development investment and construction statistics
- Property type identification (residential, commercial, etc.)
- City tier classification
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
from spiers.real-estate.spider import get_real_estate_data

results = get_real_estate_data()
results = get_real_estate_data(include_prices=True, include_market=True, include_news=False)
```

### Command Line

```bash
cd spiers/real-estate
python spider.py
```

## Output

### SQLite Database (`data/real_estate.db`)

**Table: `price_index`**
- `period`, `city`, `city_tier`, `property_type`, `property_type_cn`
- `price_index`, `avg_price`, `price_unit`, `growth_rate_mom`, `growth_rate_yoy`
- `source_url`, `scraped_at`

**Table: `market_statistics`**
- `period`, `city`, `indicator_name`, `value`, `unit`
- `growth_rate`, `growth_unit`, `property_type`, `category`
- `source_url`, `scraped_at`

**Table: `industry_news`**
- `date`, `title`, `content`, `category`, `city`, `source_url`, `scraped_at`

### JSON Exports

- `output/real_estate_prices.json`
- `output/real_estate_market.json`
- `output/real_estate_news.json`

## Property Type Coverage

| Type | Chinese | Category |
|------|---------|----------|
| Residential | 住宅 | Housing |
| Commercial Housing | 商品房 | Housing |
| Office | 办公楼 | Commercial |
| Commercial Premises | 商业营业用房 | Commercial |
| Villa | 别墅 | Luxury |
| Apartment | 公寓 | Housing |
| Affordable Housing | 经济适用房 | Government |
| Price Capped | 限价房 | Government |

## Authentication Requirements

- **No authentication required** for public market data and news
- Some detailed city-level reports may require membership

## Anti-Bot Measures

- Browser impersonation (chrome)
- Rate limiting (2s delay)
- Respectful crawling

## Known Limitations

1. Site structure may change, requiring selector updates
2. Data is updated monthly
3. Some detailed reports require paid membership
4. City-level granularity varies

## Data Quality

- **Reliability**: High (official industry association)
- **Timeliness**: Monthly updates
- **Completeness**: Major cities covered
