# MySteel (我的钢铁网) Spider

Scrapling-based spider for scraping steel pricing and market data from MySteel.

## Installation

```bash
cd /Users/chengsishi/finddata/fd-industry-data
uv sync
```

## Usage

```bash
cd spiders/mysteel

# Run with default URLs
python spider.py

# Run with custom URLs
python spider.py --urls "https://www.mysteel.com/price/" "https://www.mysteel.com/info/"

# Dry run (no data saved)
python spider.py --dry-run
```

## Data Storage

- SQLite: `data/data.sqlite`
- JSON Lines: `output/export.jsonl`

## Data Fields

- `source_url`: Original page URL
- `page_title`: Page title
- `product_name`: Steel product name
- `price`: Current price
- `change`: Price change
- `date`: Data date
- `scraped_at`: Scrape timestamp
