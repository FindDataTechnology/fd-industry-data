# GitHub China Blog Spider

Scrapling-based spider for scraping GitHub blog posts about open source in China and community updates.

## Installation

```bash
cd /Users/chengsishi/finddata/fd-industry-data
uv sync
```

## Usage

```bash
cd spiders/github-china-blog
python spider.py
python spider.py --urls "https://github.blog/2023-06-05-open-source-in-china/"
python spider.py --dry-run
```

## Data Storage

- SQLite: `data/data.sqlite`
- JSON Lines: `output/export.jsonl`

## Data Fields

- `source_url`: Original page URL
- `title`: Article title
- `author`: Article author
- `publish_date`: Publication date
- `content_summary`: Article content summary
- `tags`: Article tags
- `scraped_at`: Scrape timestamp
