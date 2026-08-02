# Agricultural Information Spider - 中国农业信息网数据爬虫

Specialized spider for extracting China Agricultural Information Network data including agricultural product prices, supply/demand data, weather impact reports, and agricultural news.

## Data Sources

- **Primary Source**: http://www.agri.cn/
- **Data Type**: Agricultural product prices, supply/demand, weather impact, news
- **Update Frequency**: Daily/Weekly (varies by data type)
- **Coverage**: National agricultural markets, weather disasters, news

## Features

- Extracts agricultural product prices from wholesale markets
- Tracks supply and demand data
- Collects weather and disaster impact reports
- Gathers agricultural news and market updates
- Rate limiting (2s delay)
- SQLite storage + JSON export
- Robots.txt compliance

## Installation

```bash
cd fd-industry-data
uv sync
```

## Usage

### Python API

```python
from spiers.agri_cn.spider import AgriCnSpider

# Run the spider
result = AgriCnSpider().start()

# Check statistics
print(f"Scraped {result.stats.items_scraped} items")
print(f"Requests: {result.stats.requests_count}")
```

### Command Line

```bash
cd spiers/agri-cn
python spider.py
```

## Output

### SQLite Database (`data/agri_cn.db`)

**Table: `product_prices`**
- `date`: Date
- `product_name`: Product name
- `category`: Product category
- `market_name`: Market name
- `province`: Province
- `price`: Price (CNY/kg)
- `price_unit`: Price unit
- `price_high`: Highest price
- `price_low`: Lowest price
- `change_pct`: Price change (%)
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `supply_demand`**
- `date`: Date
- `product_name`: Product name
- `indicator_type`: Indicator type (supply/demand)
- `value`: Value
- `unit`: Unit of measurement
- `region`: Region
- `trend`: Trend direction
- `summary`: Analysis summary
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `weather_impact`**
- `date`: Date
- `event_type`: Event type (drought/flood/typhoon/frost/hail)
- `region`: Affected region
- `severity`: Severity level
- `affected_area`: Affected area (hectares)
- `affected_unit`: Area unit
- `affected_crops`: Affected crops
- `estimated_loss`: Estimated loss (CNY)
- `loss_unit`: Loss unit
- `summary`: Event summary
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `agricultural_news`**
- `title`: News title
- `publish_date`: Publication date
- `category`: Category (market/policy/production/trade/technology)
- `source`: News source
- `summary`: News summary
- `content`: Full content (if available)
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

### JSON Export (`output/agri_cn.json`)

Clean JSON with separate arrays for each data type:
- `product_prices`: Agricultural product prices
- `supply_demand`: Supply and demand data
- `weather_impact`: Weather disaster impact reports
- `agricultural_news`: Agricultural news and updates

## Data Coverage

| Data Type | Coverage | Update Frequency |
|-----------|----------|------------------|
| Product Prices | National Wholesale Markets | Daily |
| Supply/Demand | National/Regional | Weekly |
| Weather Impact | National | Daily (as events occur) |
| Agricultural News | National | Daily |

## Authentication Requirements

**None** - All data is publicly accessible on the website. No login or API key required.

## Known Limitations

1. **Official Source**: China Agricultural Information Network is an official government platform
2. **Price Data**: Prices are from wholesale markets, may not reflect retail prices
3. **Weather Data**: Weather impact reports are event-based, not continuous
4. **News Content**: Full article content may not be available, only summaries

## Troubleshooting

### No data extracted
- Check if the website is accessible: `curl -I http://www.agri.cn/`
- Website may be temporarily unavailable
- Try increasing download delay if blocked

### Missing specific data
- Verify the data exists on the website
- Check if the URL paths are correct
- Some data may be in sub-pages not covered by the spider

## Data Quality

- **Reliability**: Very High (official government platform)
- **Timeliness**: Daily/Weekly updates
- **Completeness**: Comprehensive coverage of agricultural markets
- **Accuracy**: Official market data and government reports

## License

Data sourced from China Agricultural Information Network. Government data is generally public domain. Verify specific usage terms if needed.

## See Also

- [MOA Government Spider](../moa-gov-scraper/README.md) - Ministry of Agriculture data
- [China Flower Association Spider](../chinaflower/README.md) - Flower industry data
- [KIFC Flower Auction Spider](../flowers_kifc/README.md) - Flower auction prices
