# 东方财富期货数据 Spider

Scrapling-based spider for 东方财富期货数据 — 主力合约成交持仓龙虎榜目录。

频道页 (https://data.eastmoney.com/futures/) 的表格为 JS 渲染，静态抓取解析不到；
数据实际来自 datacenter-web JSON API（`reportName=RPT_FUTU_POSITIONCODE`，
主力合约目录，单一请求，pageSize=60）。

## Installation

```bash
pip install scrapling
```

## Usage

### Basic Usage

```bash
python spider.py
```

### Custom URLs

```bash
python spider.py --urls "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_FUTU_POSITIONCODE&columns=TRADE_MARKET_CODE,TRADE_CODE,TRADE_TYPE,SECURITY_CODE,IS_MAINCODE&filter=%28IS_MAINCODE%3D%221%22%29&pageNumber=1&pageSize=60&sortTypes=1,-1&sortColumns=IS_MAINCODE,SECURITY_CODE&source=WEB&client=WEB"
```

### Dry Run (No Save)

```bash
python spider.py --dry-run
```

## Output

- **SQLite**: `data/data.sqlite`
- **JSON Lines**: `output/export.jsonl`

## Features

- 5 rotating user agents
- 3 retries per request
- 2 second request delay
- Automatic SQLite and JSON export
- Command-line argument support
