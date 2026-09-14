# EastMoney Data Center (东方财富数据中心) Spider

Scrapling-based spider for scraping macroeconomic and financial data from EastMoney's data center.

## Installation

```bash
cd /Users/chengsishi/finddata/fd-industry-data
uv sync
```

The data.eastmoney.com CPI/PPI pages build their tables client-side, so the
spider fetches the page's own JSON API instead:
`https://datacenter-web.eastmoney.com/api/data/v1/get` (reportName
`RPT_ECONOMY_CPI` / `RPT_ECONOMY_PPI`, pure JSON without the JSONP `callback`
parameter).

## Usage

```bash
cd spiders/eastmoney-data
python spider.py
python spider.py --urls "https://datacenter-web.eastmoney.com/api/data/v1/get?columns=ALL&sortColumns=REPORT_DATE&sortTypes=-1&source=WEB&client=WEB&reportName=RPT_ECONOMY_PPI&pageNumber=1&pageSize=20"
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
