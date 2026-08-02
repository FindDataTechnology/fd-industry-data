# GitHub Dataset Topics Spider

Scrapling-based spider for scraping dataset-related repositories from GitHub Topics.

## Installation

```bash
cd /Users/chengsishi/finddata/fd-industry-data
uv sync
```

## Usage

```bash
cd spiders/github-datasets-topic
python spider.py
python spider.py --urls "https://github.com/topics/dataset"
python spider.py --dry-run
```

## Data Storage

- SQLite: `data/data.sqlite`
- JSON Lines: `output/export.jsonl`

## Data Fields

- `source_url`: Original page URL
- `repo_name`: Repository name
- `repo_url`: Repository URL
- `description`: Repository description
- `language`: Primary programming language
- `stars`: Star count
- `forks`: Fork count
- `topics`: Repository topics
- `scraped_at`: Scrape timestamp
