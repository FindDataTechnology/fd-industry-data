# Flower Auction Spider (昆明国际花卉拍卖中心)

## Overview
Scrapes auction results, price data, and market analysis from Kunming International Flower Auction Center (http://www.kifc.cn).

## Data Sources
- **Auction Results** (`/auction`): Historical auction data with prices and volumes
- **Price Data** (`/price`): Daily price information
- **Market Analysis** (`/market`): Market trends and analysis reports

## Features
- Pagination support for auction and price listings
- SQLite storage with deduplication
- JSON export
- Robots.txt compliance
- Rate limiting (2.0s delay)
- Dynamic content support via browser session

## Output Schema

### flower_auction table
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Auto-increment primary key |
| url | TEXT | Source URL |
| auction_date | TEXT | Auction date |
| date | TEXT | Date (for price data) |
| flower_name | TEXT | Flower name |
| variety | TEXT | Flower variety |
| spec | TEXT | Specification |
| quantity | TEXT | Quantity |
| unit | TEXT | Unit (枝/扎/把) |
| price | TEXT | Price |
| avg_price | TEXT | Average price |
| price_high | TEXT | High price |
| price_low | TEXT | Low price |
| volume | TEXT | Trading volume |
| total_volume | TEXT | Total volume |
| transaction_rate | TEXT | Transaction rate |
| title | TEXT | Title (for market analysis) |
| summary | TEXT | Summary |
| content | TEXT | Content |
| date_range | TEXT | Date range |
| data_type | TEXT | Type (auction/price/market/detail) |
| scraped_at | TEXT | Timestamp when scraped |

## Usage

```bash
cd fd-industry-data/spiders/flower-auction
python spider.py
```

Or programmatically:
```python
from spider import FlowerAuctionSpider

result = FlowerAuctionSpider().start()
print(f"Scraped {result.stats.items_scraped} items")
result.items.to_json("output/flower_auction.json", indent=True)
```

## Output Files
- `data/flower_auction.db` - SQLite database
- `output/flower_auction.json` - JSON export

## Notes
- Time-sensitive auction data
- Uses dynamic browser session for JavaScript-rendered content
- Respects robots.txt
- Conservative rate limiting
