# MySteel (我的钢铁网) Spider

Scrapling-based spider for scraping steel pricing data from MySteel.

Two-step server-rendered flow (the old `/price/` and `/info/` list pages are dead):

1. **Discovery** — fetch the homepage `https://www.mysteel.com/` and collect the
   statically rendered 价格行情 article links
   (`https://<channel>.mysteel.com/m/....html`).
2. **Data** — fetch each article page (bounded: up to 5 articles, 60 records per
   run) and parse the price rows from the JSON-LD price summary, merged with the
   `#marketTable` table rows (品名/规格/材质/钢厂; the table's price cells are
   encrypted in static HTML, so the JSON-LD description is the price source).

## Installation

```bash
cd /Users/chengsishi/finddata/fd-industry-data
uv sync
```

## Usage

```bash
cd spiders/mysteel

# Run with default URLs (homepage discovery flow)
python spider.py

# Run against explicit pages (homepage or article URLs)
python spider.py --urls "https://www.mysteel.com/" "https://jiancai.mysteel.com/m/26091116/57063D06324CBA6C.html"

# Dry run (no data saved)
python spider.py --dry-run
```

## Data Storage

- SQLite: `data/data.sqlite`
- JSON Lines: `output/export.jsonl`

## Data Fields

- `source_url`: URL of the article page (homepage for discovery records)
- `article_title`: Article page title
- `product_name`: Steel product name (品名)
- `spec`: Specification (规格)
- `material`: Material grade (材质)
- `mill`: Steel mill / origin (钢厂/产地)
- `price`: Current price (e.g. `3670元/吨`)
- `change`: Price change (环比…涨跌)
- `date`: Data date
- `record_kind`: `price_row` (data) or `listing_link` (discovery)
- `scraped_at`: Scrape timestamp
