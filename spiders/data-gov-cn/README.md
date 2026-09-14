# Data.gov (US Government Data) Spider

Scrapling-based spider for scraping open government datasets from Data.gov, the US federal government's open data portal. The catalog is a React SPA whose search results are server-rendered into the HTML, so the spider parses dataset cards (USWDS `usa-collection` items) from `https://catalog.data.gov/?q=<query>&sort=relevance`.

## Installation

```bash
cd /Users/chengsishi/finddata/fd-industry-data
uv sync
```

## Usage

```bash
cd spiders/data-gov-cn
python spider.py
python spider.py --urls "https://catalog.data.gov/?q=climate&sort=relevance"
python spider.py --dry-run
```

## Data Storage

- SQLite: `data/data.sqlite`
- JSON Lines: `output/export.jsonl`

## Data Fields

- `source_url`: Original page URL
- `dataset_title`: Dataset title
- `description`: Dataset description
- `organization`: Publishing organization
- `tags`: Dataset tags/keywords
- `formats`: Available data formats
- `dataset_url`: Link to dataset detail page
- `modified_date`: Last modification date
- `scraped_at`: Scrape timestamp
