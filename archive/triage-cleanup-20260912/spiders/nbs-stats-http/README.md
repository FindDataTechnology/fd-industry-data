# NBS Stats HTTP Spider

国家统计局数据查询 (HTTP版本)

## Installation
```bash
uv pip install "scrapling[fetchers]"
```

## Usage
```bash
python spider.py
python spider.py --urls "http://data.stats.gov.cn/"
python spider.py --dry-run
```

## Data Schema
- data_date: 数据日期
- indicator_name: 指标名称
- value: 数值
- unit: 单位
- source_url: 来源URL
