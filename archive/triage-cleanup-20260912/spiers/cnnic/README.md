# CNNIC Internet Statistics Spider - 中国互联网络信息中心爬虫

Crawls CNNIC for Internet development statistics and industry reports.

## Data Source

| Source | URL | Score |
|--------|-----|-------|
| 中国互联网络信息中心 | http://www.cnnic.cn/ | 90 |

## Authentication

**No authentication required.** All data is publicly available.

| Access Level | Data |
|-------------|------|
| PUBLIC (this spider) | Statistical reports, internet indicators, domain/IP stats, application usage, research reports |
| NOTES | PDF reports downloadable without auth; some bulk data may require registration |

## Data Extracted

### Internet Development Statistics
- **Internet Users** (网民规模) - Total, mobile, urban/rural breakdown
- **Penetration Rate** (互联网普及率) - Overall, urban, rural
- **Infrastructure** (基础设施):
  - IPv6 addresses
  - Domain names (.cn domains)
  - Websites count
  - Web pages count
- **Application Users** (应用用户):
  - 即时通信 (Instant Messaging)
  - 网络视频 (Online Video)
  - 短视频 (Short Video)
  - 网络直播 (Live Streaming)
  - 网络购物 (Online Shopping)
  - 网上外卖 (Food Delivery)
  - 网约车 (Ride Hailing)
  - 在线教育 (Online Education)
  - 在线医疗 (Online Medical)

### Reports Index
- Statistical Reports on Internet Development in China (中国互联网络发展状况统计报告)
- Blue Books (蓝皮书)
- White Papers (白皮书)
- Research Reports

## Usage

```bash
cd ~/finddata/fd-industry-data
uv run python spiers/cnnic/spider.py
```

## Output

- **SQLite**: `spiers/cnnic/data/cnnic_internet_data.db`
- **JSON**: `spiers/cnnic/output/internet_data.json`

## Database Schema

```sql
CREATE TABLE internet_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_period TEXT,
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
    report_number TEXT,
    publish_date TEXT,
    report_type TEXT,
    download_url TEXT,
    source_url TEXT,
    summary TEXT,
    fetched_at TEXT NOT NULL
);
```

## Notes

- CNNIC publishes the Statistical Report on Internet Development in China biannually (January and August)
- This is the authoritative source for China's internet statistics
- Data includes both aggregate indicators and detailed breakdowns
- Reports are available in Chinese with some English summaries
