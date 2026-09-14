# CNKI (中国知网) Spider

Crawls China National Knowledge Infrastructure for publicly available academic metadata.

## Data Source

| Source | URL | Score |
|--------|-----|-------|
| 中国知网 (CNKI) | https://www.cnki.net/ | 90 |

## Authentication Requirements

| Data Type | Auth Required | Notes |
|-----------|:---:|-------|
| Paper titles & abstracts | No | Publicly searchable |
| Citation/download counts | No | Visible in search results |
| Journal metadata (ISSN, CN) | No | Public catalog |
| Platform statistics | No | Homepage stats |
| Full-text PDFs | **Yes** | Institutional/personal subscription |
| Detailed citation metrics | **Yes** | CNKI API agreement required |
| Bulk data access | **Yes** | Formal agreement with CNKI |

## Data Extracted

- **Paper Metadata** - titles, authors, abstracts, keywords, citation/download counts
- **Journal Metadata** - journal names, ISSN, CN numbers, publishers, categories
- **Publication Statistics** - total documents, journal counts, thesis counts

## Usage

```bash
cd ~/finddata/fd-industry-data
uv run python spiers/cnki/spider.py
```

## Output

- **SQLite**: `spiers/cnki/data/cnki_academic_data.db`
- **JSON**: `spiers/cnki/output/academic_metadata.json`

## Database Schema

```sql
CREATE TABLE paper_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    authors TEXT,
    source_journal TEXT,
    publish_date TEXT,
    abstract TEXT,
    keywords TEXT,
    doi TEXT,
    citation_count INTEGER DEFAULT 0,
    download_count INTEGER DEFAULT 0,
    source_type TEXT,
    source_url TEXT,
    raw_data TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE journal_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    journal_name TEXT,
    issn TEXT,
    cn_number TEXT,
    publisher TEXT,
    category TEXT,
    impact_factor REAL,
    description TEXT,
    source_url TEXT,
    raw_data TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE publication_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_date TEXT,
    category TEXT,
    indicator TEXT,
    value REAL,
    unit TEXT,
    source_url TEXT,
    raw_data TEXT,
    fetched_at TEXT NOT NULL
);
```

## Rate Limiting

- 3-second delay between requests
- Respectful User-Agent header
- Only accesses publicly available pages

## Notes

- CNKI is China's largest academic database with 50M+ papers
- Public search provides titles, abstracts, and basic metrics
- Full-text access is subscription-only (institutional or personal)
- For bulk data access, contact CNKI for API licensing
- Consider alternative open-access sources: Google Scholar, Semantic Scholar
