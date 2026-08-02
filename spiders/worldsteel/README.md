# World Steel Association Spider

Scrapling-based spider for scraping steel industry data, reports, and press releases from the World Steel Association.

## Installation

```bash
cd /Users/chengsishi/finddata/fd-industry-data
uv sync
```

## Usage

```bash
cd spiders/worldsteel
python spider.py
python spider.py --urls "https://www.worldsteel.org/en/databases.html"
python spider.py --dry-run
```

## Data Storage

- SQLite: `data/data.sqlite`
- JSON Lines: `output/export.jsonl`

## Data Fields

- `source_url`: Original page URL
- `page_title`: Page title
- `report_title`: Report/article title
- `publish_date`: Publication date
- `category`: Content category
- `download_url`: Download link
- `description`: Content description
- `scraped_at`: Scrape timestamp
