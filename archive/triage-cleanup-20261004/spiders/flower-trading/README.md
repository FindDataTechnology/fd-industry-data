# Flower Trading Spider (昆明花卉交易中心)

## Overview
Scrapes flower prices, trading volumes, and variety data from Kunming Flower Trading Center (https://www.kunmingflower.com).

## Data Sources
- **Price Listings** (`/price`, `/market`, `/trade`): Daily flower prices and trading volumes
- **Variety Data** (`/variety`, `/data`): Flower variety statistics and information

## Features
- Multi-session support (HTTP + browser for dynamic content)
- Pagination support
- SQLite storage with composite unique constraints
- JSON export
- Handles both price tables and variety cards
- Robots.txt compliance

## Output Schema

### flower_trading table
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Auto-increment primary key |
| url | TEXT | Source URL |
| flower_name | TEXT | Flower name |
| variety | TEXT | Specific variety |
| spec | TEXT | Specification/grade |
| unit | TEXT | Unit (default: 枝) |
| price | TEXT | Average price |
| price_high | TEXT | High price |
| price_low | TEXT | Low price |
| volume | TEXT | Trading volume |
| trade_date | TEXT | Trading date |
| data_type | TEXT | Type: price/variety/detail |
| title | TEXT | Article title (for detail pages) |
| content | TEXT | Article content |
| scraped_at | TEXT | Timestamp when scraped |

**Unique constraint**: (url, flower_name, trade_date)

## Usage

```bash
cd fd-industry-data/spiders/flower-trading
python spider.py
```

Or programmatically:
```python
from spider import FlowerTradingSpider

result = FlowerTradingSpider().start()
print(f"Scraped {result.stats.items_scraped} items")
result.items.to_json("output/flower_trading.json", indent=True)
```

## Output Files
- `data/flower_trading.db` - SQLite database
- `output/flower_trading.json` - JSON export

## Notes
- Uses dynamic browser session for JavaScript-heavy pages
- Conservative rate limiting (2.0s delay)
- Handles multiple data formats (tables, cards, detail pages)
- Price data is time-sensitive and should be scraped regularly
