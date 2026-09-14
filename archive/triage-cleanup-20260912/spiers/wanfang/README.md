# Wanfang Data (万方数据) Spider

Crawls Wanfang Data for publicly available academic, patent, and standards metadata.

## Data Source

| Source | URL | Score |
|--------|-----|-------|
| 万方数据 | https://www.wanfangdata.com.cn/ | 85 |

## Authentication Requirements

| Data Type | Auth Required | Notes |
|-----------|:---:|-------|
| Paper titles & abstracts | No | Publicly searchable |
| Patent abstracts | No | Generally public |
| Standard metadata | No | Number, title, status, dates |
| Full-text papers | **Yes** | Institutional/personal subscription |
| Standards full-text | **Yes** | May require purchase |
| API access | **Yes** | Formal agreement required |

## Data Extracted

- **Paper Metadata** - titles, authors, abstracts, keywords, citation counts (journals, conferences, degree theses)
- **Patent Metadata** - titles, applicants, inventors, patent numbers, abstracts, IPC classification
- **Standards Metadata** - standard numbers, titles, status, issue dates, publishers
- **Platform Statistics** - total document counts by category

## Usage

```bash
cd ~/finddata/fd-industry-data
uv run python spiers/wanfang/spider.py
```

## Output

- **SQLite**: `spiers/wanfang/data/wanfang_data.db`
- **JSON**: `spiers/wanfang/output/wanfang_metadata.json`

## Database Schema

```sql
CREATE TABLE paper_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT, authors TEXT, source TEXT,
    publish_date TEXT, abstract TEXT, keywords TEXT,
    doi TEXT, citation_count INTEGER DEFAULT 0,
    document_type TEXT, source_url TEXT, raw_data TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE patent_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT, applicant TEXT, inventor TEXT,
    patent_number TEXT, application_date TEXT,
    publication_date TEXT, abstract TEXT,
    ipc_classification TEXT, source_url TEXT, raw_data TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE standard_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    standard_number TEXT, title TEXT, status TEXT,
    issue_date TEXT, implement_date TEXT,
    publisher TEXT, category TEXT,
    source_url TEXT, raw_data TEXT, fetched_at TEXT NOT NULL
);

CREATE TABLE platform_stats (
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

- Wanfang is one of China's top 3 academic databases (alongside CNKI and VIP)
- Strong in patent and standards data
- Full-text access is subscription-only
- For bulk data, contact Wanfang for API licensing
