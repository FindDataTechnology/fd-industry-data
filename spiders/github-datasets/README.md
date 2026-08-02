# GitHub Awesome China Dataset Spider

## Overview
Scrapes dataset repositories from GitHub, focusing on China-related data collections and awesome lists.

## Data Sources
- **Awesome China Data** (`github.com/awesome-china/data`): Curated list of China datasets
- **Topics**: China-data, Chinese-data, China-dataset
- **Repository Pages**: Individual dataset repositories

## Features
- Repository metadata extraction
- README parsing for data links
- Topic-based discovery
- SQLite storage
- JSON export
- Robots.txt compliance

## Output Schema

### github_datasets table
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Auto-increment primary key |
| url | TEXT | Repository URL (unique per data_type) |
| repo_name | TEXT | Repository name |
| owner | TEXT | Repository owner/organization |
| description | TEXT | Repository description |
| stars | TEXT | Star count |
| forks | TEXT | Fork count |
| language | TEXT | Primary programming language |
| topics | TEXT | Topic tags (JSON array) |
| license | TEXT | License type |
| last_commit | TEXT | Last commit timestamp |
| content | TEXT | README content (for readme type) |
| data_links | TEXT | Extracted data download links (JSON array) |
| repo_url | TEXT | Parent repository URL (for readme type) |
| data_type | TEXT | Type: repo or readme |
| scraped_at | TEXT | Timestamp when scraped |

## Usage

```bash
cd fd-industry-data/spiders/github-datasets
python spider.py
```

Or programmatically:
```python
from spider import GithubDatasetsSpider

result = GithubDatasetsSpider().start()
print(f"Scraped {result.stats.items_scraped} items")
result.items.to_json("output/github_datasets.json", indent=True)
```

## Output Files
- `data/github_datasets.db` - SQLite database
- `output/github_datasets.json` - JSON export

## Notes
- Focuses on metadata, not full dataset downloads
- Extracts data file links from READMEs
- Respects GitHub's rate limits
- Robots.txt compliant
