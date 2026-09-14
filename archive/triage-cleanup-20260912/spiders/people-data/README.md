# 人民网数据中心

## Overview
Scrapy-based web scraper for 人民网数据中心.

## Source
- **URL**: https://data.people.com.cn/
- **Data Type**: news-archive
- **Score**: High Priority (>=85)

## Features
- Anti-bot protection using Scrapling
- SQLite storage
- JSON export support
- CLI interface

## Usage
```bash
python spider.py
```

## Data Schema
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| title | TEXT | Item title |
| url | TEXT | Unique URL |
| content | TEXT | Page content |
| published_date | TEXT | Publication date |
| category | TEXT | Category label |
| source_url | TEXT | Original source |
| scraped_at | TEXT | ISO timestamp |

## License
MIT
