# Flower Association Spider (中国花卉协会)

## Overview
Scrapes news, policies, market data, and statistics from the China Flower Association website (https://www.chinaflower.org.cn).

## Data Sources
- **News/Industry News** (`/list/27`): Latest flower industry news
- **Policies & Regulations** (`/list/28`): Government policies affecting the flower industry
- **Market Trends** (`/list/29`): Market analysis and trends
- **Statistics** (`/list/30`): Statistical data and reports

## Features
- Pagination support for listing pages
- SQLite storage with deduplication
- JSON export
- Robots.txt compliance
- Rate limiting (1.5s delay)

## Output Schema

### flower_association table
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Auto-increment primary key |
| url | TEXT | Source URL (unique) |
| title | TEXT | Article title |
| content | TEXT | Article content (max 5000 chars) |
| publish_date | TEXT | Publication date |
| source | TEXT | Data source name |
| category | TEXT | Category (行业新闻/政策法规/市场行情/统计数据) |
| scraped_at | TEXT | Timestamp when scraped |

## Usage

```bash
cd fd-industry-data/spiders/flower-association
python spider.py
```

Or programmatically:
```python
from spider import FlowerAssociationSpider

result = FlowerAssociationSpider().start()
print(f"Scraped {result.stats.items_scraped} items")
result.items.to_json("output/flower_association.json", indent=True)
```

## Output Files
- `data/flower_association.db` - SQLite database
- `output/flower_association.json` - JSON export

## Notes
- Respects robots.txt
- Conservative rate limiting to avoid overloading the server
- Content truncated to 5000 characters to save storage
