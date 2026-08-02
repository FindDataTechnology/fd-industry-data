# CEA E-commerce Spider - 中国电子商务协会数据爬虫

Specialized spider for extracting e-commerce market data, online retail statistics, and platform performance from China E-commerce Association.

## Data Sources

- **Primary Source**: http://www.ec.com.cn/
- **Data Type**: E-commerce market data, online retail statistics, platform performance
- **Update Frequency**: Monthly
- **Focus**: Online retail volume, e-commerce GMV, cross-border e-commerce, digital trade

## Features

- Extracts e-commerce GMV and market size statistics
- Platform performance tracking (GMV, market share, user count)
- Cross-border e-commerce data (import/export)
- E-commerce category identification (B2B, B2C, C2C, live commerce, etc.)
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
from spiers.cea_ecommerce.spider import get_cea_ecommerce_data

results = get_cea_ecommerce_data()
results = get_cea_ecommerce_data(include_market=True, include_platform=True, include_crossborder=False, include_news=False)
```

### Command Line

```bash
cd spiers/cea-ecommerce
python spider.py
```

## Output

### SQLite Database (`data/cea_ecommerce.db`)

**Table: `ecommerce_market`**
- `period`, `category`, `category_cn`, `indicator_name`, `value`, `unit`
- `growth_rate`, `growth_unit`, `region`, `source_url`, `scraped_at`

**Table: `platform_performance`**
- `period`, `platform_name`, `gmv`, `gmv_unit`, `market_share`, `user_count`, `order_count`
- `source_url`, `scraped_at`

**Table: `crossborder_stats`**
- `period`, `direction`, `value`, `unit`, `growth_rate`, `growth_unit`, `category`
- `source_url`, `scraped_at`

**Table: `industry_news`**
- `date`, `title`, `content`, `category`, `source_url`, `scraped_at`

### JSON Exports

- `output/cea_ecommerce_market.json`
- `output/cea_ecommerce_platform.json`
- `output/cea_ecommerce_crossborder.json`
- `output/cea_ecommerce_news.json`

## E-commerce Category Coverage

| Category | Chinese | Type |
|----------|---------|------|
| Online Retail | 网络零售 | Retail |
| B2B | B2B | Enterprise |
| B2C | B2C | Consumer |
| C2C | C2C | Peer-to-peer |
| Cross-border | 跨境电商 | International |
| Social Commerce | 社交电商 | Social |
| Live Commerce | 直播电商 | Live streaming |
| Rural E-commerce | 农村电商 | Rural |
| Fresh E-commerce | 生鲜电商 | Fresh food |
| Community Group Buy | 社区团购 | Community |

## Authentication Requirements

- **No authentication required** for public market data and news
- Some detailed platform reports may require membership
- Cross-border e-commerce statistics are publicly available

## Anti-Bot Measures

- Browser impersonation (chrome)
- Rate limiting (2s delay between requests)
- Respectful crawling with proper User-Agent

## Known Limitations

1. Site structure may change, requiring selector updates
2. Platform-level data availability varies
3. Some detailed reports require paid membership
4. Cross-border data may have reporting lag

## Data Quality

- **Reliability**: High (official industry association)
- **Timeliness**: Monthly updates
- **Completeness**: Comprehensive e-commerce market coverage
