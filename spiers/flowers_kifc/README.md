# KIFC Flower Auction Spider - 昆明花卉交易中心拍卖数据爬虫

Specialized spider for extracting Kunming International Flower Center (KIFC) auction data including daily prices, trading volumes, market trends, and export statistics.

## Data Sources

- **Primary Source**: http://www.kifc.cn
- **Data Type**: Flower auction prices, trading volumes, market trends, export data
- **Update Frequency**: Daily (prices/volumes), Weekly (trends), Monthly (exports)
- **Coverage**: Fresh-cut flowers, auction market data

## Features

- Extracts daily flower auction prices
- Tracks trading volumes by flower type
- Collects market trends and analysis
- Gathers export data and statistics
- Rate limiting (1.5s delay)
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
from spiers.flowers_kifc.spider import KIFCFlowerSpider

# Run the spider
result = KIFCFlowerSpider().start()

# Check statistics
print(f"Scraped {result.stats.items_scraped} items")
print(f"Requests: {result.stats.requests_count}")
```

### Command Line

```bash
cd spiers/flowers_kifc
python spider.py
```

## Output

### SQLite Database (`data/kifc_flowers.db`)

**Table: `flower_prices`**
- `date`: Trading date
- `flower_name`: Flower variety
- `variety`: Sub-variety
- `spec`: Specification/grade
- `price`: Auction price (CNY/stem)
- `price_high`: Highest price
- `price_low`: Lowest price
- `unit`: Price unit
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `trading_volumes`**
- `date`: Trading date
- `flower_name`: Flower variety
- `category`: Category
- `volume`: Trading volume (stems)
- `volume_unit`: Volume unit
- `turnover`: Trading turnover
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `market_trends`**
- `period`: Analysis period
- `flower_name`: Flower variety
- `trend_type`: Trend type
- `value`: Trend value
- `change_pct`: Change percentage
- `summary`: Analysis summary
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `export_data`**
- `period`: Export period
- `flower_name`: Flower variety
- `destination`: Export destination
- `quantity`: Export quantity (kg)
- `quantity_unit`: Quantity unit
- `value_usd`: Value in USD
- `value_cny`: Value in CNY
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

### JSON Export (`output/kifc_flowers.json`)

Clean JSON with separate arrays for each data type:
- `flower_prices`: Daily auction prices
- `trading_volumes`: Trading volume data
- `market_trends`: Market trend analysis
- `export_data`: Export statistics

## Data Coverage

| Data Type | Coverage | Update Frequency |
|-----------|----------|------------------|
| Auction Prices | Daily by Variety | Daily |
| Trading Volumes | By Flower Type | Daily |
| Market Trends | Analysis Reports | Weekly |
| Export Data | By Destination | Monthly |

## Authentication Requirements

**None** - All auction data is publicly accessible on the KIFC website. No login or API key required.

## Known Limitations

1. **Website Access**: KIFC website may have limited accessibility outside China
2. **Data Delay**: Some data may be delayed or only available after market close
3. **Historical Data**: Limited historical data available online
4. **Language**: Website is primarily in Chinese

## Troubleshooting

### No data extracted
- Check if the website is accessible: `curl -I http://www.kifc.cn`
- Website may be temporarily unavailable
- Try increasing download delay if blocked

### Missing specific data
- Verify the data exists on the website
- Check if the URL paths are correct
- Some data may be in sub-pages not covered by the spider

## Data Quality

- **Reliability**: High (official auction center)
- **Timeliness**: Daily updates for prices and volumes
- **Completeness**: Covers major flower varieties traded at KIFC
- **Accuracy**: Official auction market data

## License

Data sourced from Kunming International Flower Center (KIFC). For commercial use, verify licensing terms with KIFC directly.

## See Also

- [China Flower Association Spider](../chinaflower/README.md) - Flower industry statistics
- [MOA Government Spider](../moa-gov-scraper/README.md) - Agricultural data
- [Agricultural Information Spider](../agri-cn/README.md) - Agricultural product prices
