# National Library of China (国家图书馆) Spider

Crawls the National Library of China for publicly available catalog, digital collection, and exhibition data.

## Data Source

| Source | URL | Score |
|--------|-----|-------|
| 国家图书馆 | https://www.nlc.cn/ | 80 |

## Authentication Requirements

| Data Type | Auth Required | Notes |
|-----------|:---:|-------|
| OPAC catalog | No | Public search |
| Digital collections metadata | No | Publicly browsable |
| Exhibition/event info | No | Public information |
| Library statistics | No | Published on website |
| Rare book full-text | **Yes** | On-site access at NLC required |
| Special collection access | **Partial** | Some require registration |

## Data Extracted

- **Catalog Records** - book titles, authors, publishers, ISBNs, categories
- **Digital Collections** - collection names, descriptions, item counts, access types
- **Exhibitions & Events** - titles, dates, locations, descriptions
- **Library Statistics** - total collection size, rare books, digital resources, visitor counts

## Usage

```bash
cd ~/finddata/fd-industry-data
uv run python spiers/nlc/spider.py
```

## Output

- **SQLite**: `spiers/nlc/data/nlc_collection_data.db`
- **JSON**: `spiers/nlc/output/nlc_collection_metadata.json`

## Database Schema

```sql
CREATE TABLE catalog_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT, author TEXT, publisher TEXT,
    publish_date TEXT, isbn TEXT, category TEXT,
    collection_type TEXT, description TEXT,
    source_url TEXT, raw_data TEXT, fetched_at TEXT NOT NULL
);

CREATE TABLE digital_collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT, collection_name TEXT, category TEXT,
    description TEXT, item_count INTEGER DEFAULT 0,
    access_type TEXT, source_url TEXT, raw_data TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE exhibitions_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT, event_type TEXT, start_date TEXT,
    end_date TEXT, location TEXT, description TEXT,
    source_url TEXT, raw_data TEXT, fetched_at TEXT NOT NULL
);

CREATE TABLE library_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_date TEXT, category TEXT, indicator TEXT,
    value REAL, unit TEXT, source_url TEXT, raw_data TEXT,
    fetched_at TEXT NOT NULL
);
```

## Rate Limiting

- 3-second delay between requests
- Respectful User-Agent header
- Only accesses publicly available pages

## Notes

- NLC is China's national library with 40M+ volumes
- Rich digital collections including historical archives
- OPAC interface provides public catalog search
- Some rare book databases require on-site access
- Good source for publication metadata and historical document info
