# 199IT Internet Data Center Spider

Crawls 199IT for publicly available industry research reports, statistics, and tech trends.

## Data Source

| Source | URL | Score |
|--------|-----|-------|
| 199IT 互联网数据中心 | https://www.199it.com/ | 70 |

## Authentication Requirements

| Data Type | Auth Required | Notes |
|-----------|:---:|-------|
| Report titles & summaries | No | Publicly visible |
| Industry statistics | No | Extracted from public articles |
| Tech trend articles | No | Publicly accessible |
| Full report PDFs | **Partial** | Some require registration or paid membership |
| Premium reports | **Yes** | Paid membership required |

## Data Extracted

- **Research Reports** - titles, summaries, source organizations, dates, categories, tags
- **Industry Statistics** - user scale, market size, growth rates, penetration rates
- **Tech Trends** - trending topics, descriptions, dates

## Categories Covered

- Internet (互联网)
- E-commerce (电子商务)
- Mobile (移动互联网)
- Social Media (社交媒体)
- Cloud Computing (云计算)
- Artificial Intelligence (人工智能)
- FinTech (金融科技)

## Usage

```bash
cd ~/finddata/fd-industry-data
uv run python spiers/199it/spider.py
```

## Output

- **SQLite**: `spiers/199it/data/199it_research_data.db`
- **JSON**: `spiers/199it/output/199it_research_reports.json`

## Database Schema

```sql
CREATE TABLE research_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT, category TEXT, source_organization TEXT,
    publish_date TEXT, summary TEXT, tags TEXT,
    report_type TEXT, download_url TEXT,
    source_url TEXT, raw_data TEXT, fetched_at TEXT NOT NULL
);

CREATE TABLE industry_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_date TEXT, category TEXT, indicator TEXT,
    value REAL, unit TEXT, source_report TEXT,
    source_url TEXT, raw_data TEXT, fetched_at TEXT NOT NULL
);

CREATE TABLE trend_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trend_date TEXT, category TEXT, topic TEXT,
    description TEXT, source_url TEXT, raw_data TEXT,
    fetched_at TEXT NOT NULL
);
```

## Rate Limiting

- 3-second delay between requests
- Respectful User-Agent header
- Only accesses publicly available pages

## Notes

- 199IT aggregates research reports from hundreds of sources
- Good for tracking internet industry trends and market data
- Report summaries are public; full PDFs may need membership
- Categories cover the full spectrum of tech/internet industry
