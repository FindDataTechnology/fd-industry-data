# China Association of Science and Technology (中国科协) Spider

Crawls CAST for publicly available science & technology statistics, conference info, and research data.

## Data Source

| Source | URL | Score |
|--------|-----|-------|
| 中国科学技术协会 | http://www.cast.org.cn/ | 85 |

## Authentication Requirements

| Data Type | Auth Required | Notes |
|-----------|:---:|-------|
| News articles | No | Publicly accessible |
| Conference information | No | Public listings |
| Publications & reports | No | Free downloads |
| Institution data | No | Public directory |
| Science statistics | No | Published openly |
| Conference registration | **Yes** | Attending conferences may require login |

**Note:** CAST is a government organization — virtually all content is publicly accessible.

## Data Extracted

- **News Articles** - science & tech news, policy updates, CAST activities
- **Conferences** - academic conferences, forums, summits organized by CAST
- **Publications** - reports, journals, standards, blue books
- **Institutions** - academic societies, research institutes, universities, labs
- **Science Statistics** - S&T workforce, R&D personnel, academicians, funding, patents, papers

## Usage

```bash
cd ~/finddata/fd-industry-data
uv run python spiers/cast/spider.py
```

## Output

- **SQLite**: `spiers/cast/data/cast_science_data.db`
- **JSON**: `spiers/cast/output/cast_science_tech_data.json`

## Database Schema

```sql
CREATE TABLE news_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT, publish_date TEXT, category TEXT,
    summary TEXT, source_url TEXT, raw_data TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE conferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT, conference_date TEXT, location TEXT,
    organizer TEXT, description TEXT,
    source_url TEXT, raw_data TEXT, fetched_at TEXT NOT NULL
);

CREATE TABLE publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT, publish_date TEXT, pub_type TEXT,
    authors TEXT, description TEXT, download_url TEXT,
    source_url TEXT, raw_data TEXT, fetched_at TEXT NOT NULL
);

CREATE TABLE institution_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, category TEXT, location TEXT,
    description TEXT, source_url TEXT, raw_data TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE science_stats (
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

- CAST oversees 200+ national academic societies
- Authoritative source for China S&T statistics
- All content is public (government organization)
- Good source for academic conference calendar
- Publishes annual science & technology development reports
