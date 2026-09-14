# CSIA Software Industry Spider - 中国软件行业协会爬虫

Crawls CSIA for software industry statistics, enterprise rankings, and market reports.

## Data Source

| Source | URL | Score |
|--------|-----|-------|
| 中国软件行业协会 | http://www.csia.org.cn/ | 85 |

## Authentication

**No authentication required.** All data is publicly available.

| Access Level | Data |
|-------------|------|
| PUBLIC (this spider) | Software industry stats, IT service data, enterprise rankings, industry reports |
| NOTES | Monthly data from MIIT; Annual enterprise rankings |

## Data Extracted

### Software Industry Statistics
- **Revenue** (收入):
  - 软件业务收入 (Software business revenue)
  - 软件产品收入 (Software product revenue)
  - 信息技术服务收入 (IT service revenue)
  - 嵌入式系统软件收入 (Embedded software revenue)
  - 信息安全收入 (Information security revenue)
  - 云计算收入 (Cloud computing revenue)
  - 大数据收入 (Big data revenue)
  - 人工智能收入 (AI revenue)
- **Exports** (出口) - Software export value
- **Employment** (从业人员) - Software industry workforce
- **Enterprises** (企业数量) - Number of software companies

### Enterprise Rankings
- 软件百强企业 (Software Top 100)
- IT服务百强 (IT Service Top 100)
- 信息服务企业排名 (Information Service Rankings)

### Reports & Publications
- **White Papers** (白皮书)
- **Blue Books** (蓝皮书)
- **Research Reports** (研究报告)
- **Industry Analysis** (行业分析)

## Usage

```bash
cd ~/finddata/fd-industry-data
uv run python spiers/csia/spider.py
```

## Output

- **SQLite**: `spiers/csia/data/csia_software_data.db`
- **JSON**: `spiers/csia/output/software_data.json`

## Database Schema

```sql
CREATE TABLE software_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_period TEXT,
    indicator_name TEXT,
    indicator_name_cn TEXT,
    value REAL,
    unit TEXT,
    category TEXT,
    sub_category TEXT,
    source_url TEXT,
    raw_data TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE enterprises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    enterprise_name TEXT,
    enterprise_name_cn TEXT,
    ranking INTEGER,
    category TEXT,
    revenue REAL,
    revenue_unit TEXT,
    stat_year TEXT,
    source_url TEXT,
    raw_data TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    title_cn TEXT,
    publish_date TEXT,
    report_type TEXT,
    category TEXT,
    source_url TEXT,
    summary TEXT,
    fetched_at TEXT NOT NULL
);
```

## Notes

- CSIA is the national industry association for software and IT services
- Publishes monthly software industry statistics (sourced from MIIT)
- Annual software Top 100 enterprise rankings are highly influential
- Covers emerging areas: cloud, big data, AI, information security
- Industry reports track technology trends and market development
