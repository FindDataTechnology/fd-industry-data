# EastMoney Data Center (东方财富数据中心) Spider

Scrapling-based spider for scraping macroeconomic and financial data from EastMoney's data center.

## Installation

```bash
cd /Users/chengsishi/finddata/fd-industry-data
uv sync
```

## Usage

```bash
cd spiders/eastmoney-data
python spider.py
python spider.py --urls "https://data.eastmoney.com/cjsj/cpi.html"
python spider.py --dry-run
```

## Data Storage

- SQLite: `data/data.sqlite`
- JSON Lines: `output/export.jsonl`

## Data Fields

- `source_url`: Original page URL
- `page_title`: Page title
- `indicator_name`: Economic indicator name
- `indicator_value`: Indicator value
- `period`: Time period
- `change_value`: Change amount
- `category`: Data category
- `scraped_at`: Scrape timestamp
