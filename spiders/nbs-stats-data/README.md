# NBS Stats Data Query (国家统计局数据查询) Spider

Scrapling-based spider for scraping statistical data from China's National Bureau of Statistics data query portal.

## Installation

```bash
cd /Users/chengsishi/finddata/fd-industry-data
uv sync
```

## Usage

```bash
cd spiders/nbs-stats-data
python spider.py
python spider.py --urls "http://data.stats.gov.cn/easyquery.htm?cn=C01"
python spider.py --dry-run
```

## Data Storage

- SQLite: `data/data.sqlite`
- JSON Lines: `output/export.jsonl`

## Data Fields

- `source_url`: Original page URL
- `indicator_name`: Statistical indicator name
- `indicator_value`: Indicator value
- `period`: Time period
- `unit`: Measurement unit
- `region`: Geographic region
- `category`: Data category
- `scraped_at`: Scrape timestamp
