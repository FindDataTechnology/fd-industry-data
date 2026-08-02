# GitHub Awesome Public Datasets Spider

Scrapling-based spider for scraping curated public dataset listings from the Awesome Public Datasets repository on GitHub.

## Installation

```bash
cd /Users/chengsishi/finddata/fd-industry-data
uv sync
```

## Usage

```bash
cd spiders/github-awesome-datasets
python spider.py
python spider.py --urls "https://github.com/awesomedata/awesome-public-datasets"
python spider.py --dry-run
```

## Data Storage

- SQLite: `data/data.sqlite`
- JSON Lines: `output/export.jsonl`

## Data Fields

- `source_url`: Original page URL
- `dataset_name`: Dataset name
- `description`: Dataset description
- `category`: Category (from headings)
- `url`: Link to dataset
- `stars`: Star count
- `scraped_at`: Scrape timestamp
