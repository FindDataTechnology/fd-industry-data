# China Internet Association (ISC) Spider - 中国互联网协会爬虫

Crawls ISC for internet industry data, enterprise rankings, and governance reports.

## Data Source

| Source | URL | Score |
|--------|-----|-------|
| 中国互联网协会 | http://www.isc.org.cn/ | 85 |

## Authentication

**No authentication required.** All data is publicly available.

| Access Level | Data |
|-------------|------|
| PUBLIC (this spider) | Internet economy stats, Internet 100 rankings, industry reports, governance documents |
| NOTES | Annual Internet 100 rankings since 2012; Digital economy statistics |

## Data Extracted

### Internet Economy Statistics
- **Enterprises** (企业) - Number of internet companies
- **Revenue** (收入) - Internet business revenue, platform revenue
- **Digital Economy** (数字经济):
  - 数字经济规模 (Digital economy size)
  - 数字经济占GDP比重 (Digital economy as % of GDP)
- **E-commerce** (电子商务):
  - 电子商务交易额 (E-commerce GMV)
  - 网络零售额 (Online retail)
  - 移动支付交易规模 (Mobile payment volume)

### China Internet 100 Rankings (中国互联网企业百强)
- Annual ranking of top 100 internet companies in China
- Includes revenue data and main business areas
- Published since 2012

### Reports & Governance
- **White Papers** (白皮书)
- **Blue Books** (蓝皮书)
- **Research Reports** (研究报告)
- **Governance Documents** (治理文件)
- **Self-regulation Standards** (自律规范)

## Usage

```bash
cd ~/finddata/fd-industry-data
uv run python spiers/isc/spider.py
```

## Output

- **SQLite**: `spiers/isc/data/isc_internet_data.db`
- **JSON**: `spiers/isc/output/internet_industry_data.json`

## Database Schema

```sql
CREATE TABLE internet_industry_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_year TEXT,
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

CREATE TABLE internet_enterprises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    enterprise_name TEXT,
    enterprise_name_cn TEXT,
    ranking INTEGER,
    category TEXT,
    main_business TEXT,
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

- ISC is the national industry association for China's internet sector
- Publishes the influential China Internet 100 rankings annually
- Covers digital economy, platform governance, and industry self-regulation
- Reports track internet industry development trends
- Governance research covers platform economy regulations
