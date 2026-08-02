# GitHub Chinese Datasets Spider

Scrapling-based spider for scraping Chinese dataset repositories from GitHub, including the datasets organization and China-related data resources.

## Installation

```bash
cd /Users/chengsishi/finddata/fd-industry-data
uv sync
```

## Usage

```bash
cd spiders/github-chinese-datasets
python spider.py
python spider.py --urls "https://github.com/datasets/chinese-datasets"
python spider.py --dry-run
```

## Data Storage

- SQLite: `data/data.sqlite`
- JSON Lines: `output/export.jsonl`

## Data Fields

- `source_url`: Original page URL
- `dataset_name`: Dataset name
- `description`: Dataset description
- `dataset_url`: Link to dataset
- `category`: Dataset category
- `format_type`: Data format
- `size`: Dataset size
- `scraped_at`: Scrape timestamp
