# Yunnan Flowers (云南省花卉产业信息网) Spider

Scrapling-based spider for scraping flower industry data, pricing, and market information from Yunnan's flower industry information network.

## Installation

```bash
cd /Users/chengsishi/finddata/fd-industry-data
uv sync
```

## Usage

```bash
cd spiders/flowers-yunnan
python spider.py
python spider.py --urls "https://flowers.yunnan.gov.cn/price"
python spider.py --dry-run
```

## Data Storage

- SQLite: `data/data.sqlite`
- JSON Lines: `output/export.jsonl`

## Data Fields

- `source_url`: Original page URL
- `page_title`: Page title
- `flower_name`: Flower variety name
- `price`: Current price
- `unit`: Price unit
- `category`: Flower category
- `origin`: Place of origin
- `publish_date`: Price date
- `scraped_at`: Scrape timestamp
