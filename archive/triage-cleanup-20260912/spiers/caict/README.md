# CAICT ICT Statistics Spider - 中国信息通信研究院爬虫

Crawls CAICT for ICT industry statistics, technology reports, and policy analysis.

## Data Source

| Source | URL | Score |
|--------|-----|-------|
| 中国信息通信研究院 | http://www.caict.ac.cn/ | 88 |

## Authentication

**No authentication required.** All data is publicly available.

| Access Level | Data |
|-------------|------|
| PUBLIC (this spider) | Telecom statistics, internet infrastructure, technology reports, policy analysis, white papers |
| NOTES | CAICT is the authoritative source for China's ICT industry data |

## Data Extracted

### Telecommunications Statistics
- **Revenue** (业务收入) - Telecom business revenue
- **Users** (用户数):
  - 移动电话用户 (Mobile phone users)
  - 5G手机用户 (5G mobile users)
  - 固定电话用户 (Fixed phone users)
  - 宽带用户 (Broadband users)
  - 光纤宽带用户 (Fiber broadband users)
  - 物联网终端用户 (IoT terminal users)
- **Traffic** (流量):
  - 移动互联网流量 (Mobile internet traffic)
  - 月户均流量 (Average monthly traffic per user)

### Internet Infrastructure
- 互联网宽带接入端口 (Internet broadband ports)
- App数量 (Number of apps)

### Reports & Publications
- **White Papers** (白皮书) - Technology white papers
- **Blue Books** (蓝皮书) - Industry blue books
- **Research Reports** (研究报告) - Research analysis
- **Policy Analysis** (政策分析) - Policy recommendations

### Technology Areas Covered
- 5G/6G mobile communications
- Artificial Intelligence (AI)
- Internet of Things (IoT)
- Cloud Computing
- Big Data
- Industrial Internet

## Usage

```bash
cd ~/finddata/fd-industry-data
uv run python spiers/caict/spider.py
```

## Output

- **SQLite**: `spiers/caict/data/caict_ict_data.db`
- **JSON**: `spiers/caict/output/ict_data.json`

## Database Schema

```sql
CREATE TABLE ict_stats (
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

CREATE TABLE reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    title_cn TEXT,
    publish_date TEXT,
    report_type TEXT,
    category TEXT,
    download_url TEXT,
    source_url TEXT,
    summary TEXT,
    fetched_at TEXT NOT NULL
);
```

## Notes

- CAICT is the research institute under MIIT (Ministry of Industry and Information Technology)
- Publishes monthly telecommunications statistics
- Authoritative source for China's ICT industry data
- Technology reports cover emerging technologies and industry trends
- Policy research supports government decision-making
