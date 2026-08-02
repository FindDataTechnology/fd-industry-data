# Kaggle Datasets Spider

## Overview
Scrapes dataset metadata from Kaggle (https://www.kaggle.com/datasets), focusing on flower, China, and agriculture-related datasets.

## Data Sources
- **Datasets List** (`/datasets`): Browse all available datasets
- **Search Results** (`/search`): Search for specific datasets by keywords
- **Dataset Details**: Individual dataset pages with full metadata

## Features
- Search functionality for specific keywords
- Pagination support
- Dataset detail extraction including file information
- SQLite storage with deduplication
- JSON export
- Stealth mode for anti-bot bypass
- Robots.txt compliance

## Output Schema

### kaggle_datasets table
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Auto-increment primary key |
| url | TEXT | Dataset URL |
| dataset_id | TEXT | Kaggle dataset ID |
| title | TEXT | Dataset title |
| author | TEXT | Dataset author |
| description | TEXT | Dataset description |
| tags | TEXT | JSON array of tags |
| votes | TEXT | Number of votes |
| usability | TEXT | Usability score |
| usability_rating | TEXT | Detailed usability rating |
| size | TEXT | Dataset size |
| downloads | TEXT | Number of downloads |
| license | TEXT | Dataset license |
| file_count | TEXT | Number of files |
| last_updated | TEXT | Last update timestamp |
| file_name | TEXT | Individual file name |
| file_size | TEXT | Individual file size |
| file_type | TEXT | Individual file type |
| download_url | TEXT | File download URL |
| data_type | TEXT | Type: dataset_card/dataset_detail/file_info |
| scraped_at | TEXT | Timestamp when scraped |

## Usage

```bash
cd fd-industry-data/spiders/kaggle
python spider.py
```

Or programmatically:
```python
from spider import KaggleDatasetsSpider

result = KaggleDatasetsSpider().start()
print(f"Scraped {result.stats.items_scraped} items")
result.items.to_json("output/kaggle_datasets.json", indent=True)
```

## Output Files
- `data/kaggle.db` - SQLite database
- `output/kaggle_datasets.json` - JSON export

## Notes
- Uses stealth mode for Kaggle's anti-bot protection
- Focuses on metadata extraction, not full dataset downloads
- Tags stored as JSON arrays
- File information extracted separately for each dataset
- Respects robots.txt and rate limits
