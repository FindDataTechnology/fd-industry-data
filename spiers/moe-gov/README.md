# Ministry of Education (MOE) Spider - 教育部数据爬虫

Crawls the Ministry of Education of China for education statistics, expenditure data, and policy documents.

## Data Source

| Source | URL | Score |
|--------|-----|-------|
| 中华人民共和国教育部 | http://www.moe.gov.cn/ | 95 |

## Authentication

**No authentication required.** All data is publicly available on the MOE website.

| Access Level | Data |
|-------------|------|
| PUBLIC (this spider) | Statistical communiques, education expenditure, policy documents, enrollment data |
| NOT COVERED | Interactive data query tools (may have CAPTCHA), internal administrative documents |

## Data Extracted

### Education Statistics
- **Schools** (学校数) - Total schools by level
- **Enrollment** (在校生) - Students by education level:
  - 学前教育 (Preschool)
  - 小学 (Primary)
  - 初中 (Junior Secondary)
  - 普通高中 (Senior Secondary)
  - 中等职业学校 (Vocational Secondary)
  - 高等教育 (Higher Education)
  - 研究生 (Graduate)
- **Teachers** (专任教师) - Teaching staff counts
- **Enrollment Rates** (毛入学率/入园率) - Gross enrollment ratios
- **Education Expenditure** (教育经费) - Fiscal education budget, GDP ratio

### Policy Documents
- 通知 (Notices)
- 意见 (Opinions)
- 规定 (Regulations)
- 办法 (Measures)
- 公报 (Communiques)

## Usage

```bash
cd ~/finddata/fd-industry-data
uv run python spiers/moe-gov/spider.py
```

## Output

- **SQLite**: `spiers/moe-gov/data/moe_education_data.db`
- **JSON**: `spiers/moe-gov/output/education_data.json`

## Database Schema

```sql
CREATE TABLE education_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_year TEXT,
    indicator_name TEXT,
    indicator_name_cn TEXT,
    value REAL,
    unit TEXT,
    education_level TEXT,
    education_level_cn TEXT,
    region TEXT,
    category TEXT,
    source_url TEXT,
    raw_data TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE policy_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_number TEXT,
    title TEXT,
    title_cn TEXT,
    issue_date TEXT,
    category TEXT,
    source_url TEXT,
    summary TEXT,
    fetched_at TEXT NOT NULL
);
```

## Notes

- Data covers annual national education statistics
- Statistical communiques published annually (typically late Q1 of following year)
- Education expenditure data follows fiscal year calendar
- Policy documents are official regulatory publications
