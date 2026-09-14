# Logistics Spider - 中国物流与采购联合会数据爬虫

Specialized spider for extracting logistics industry data, PMI indices, and supply chain statistics from CLPF.

## Data Sources

- **Primary Source**: http://www.chinawuliu.com.cn/
- **Data Type**: PMI indices, logistics statistics, transportation data, supply chain data
- **Update Frequency**: Monthly
- **Focus**: PMI (Purchasing Managers' Index), logistics cost, freight volume

## Features

- Extracts PMI index data (manufacturing, non-manufacturing)
- Sub-indicator tracking (new orders, production, employment, etc.)
- Logistics cost and freight volume statistics
- Rate limiting (2s delay)
- SQLite storage + JSON export

## Installation

```bash
cd fd-industry-data
uv sync
```

## Usage

### Python API

```python
from spiers.logistics.spider import get_logistics_data

results = get_logistics_data()
results = get_logistics_data(include_pmi=True, include_stats=True, include_news=False)
```

### Command Line

```bash
cd spiers/logistics
python spider.py
```

## Output

### SQLite Database (`data/logistics.db`)

**Table: `pmi_data`**
- `period`, `indicator`, `indicator_cn`, `value`, `change`, `threshold` (50.0)
- `category`, `source_url`, `scraped_at`

**Table: `logistics_statistics`**
- `period`, `indicator_name`, `value`, `unit`, `growth_rate`, `growth_unit`
- `category`, `source_url`, `scraped_at`

**Table: `industry_news`**
- `date`, `title`, `content`, `category`, `source_url`, `scraped_at`

### JSON Exports

- `output/logistics_pmi.json`
- `output/logistics_statistics.json`
- `output/logistics_news.json`

## PMI Indicator Coverage

| Indicator | Chinese | Description |
|-----------|---------|-------------|
| PMI | PMI/采购经理指数 | Composite index |
| New Orders | 新订单 | Demand indicator |
| Production | 生产 | Output indicator |
| Employment | 从业人员 | Labor market |
| Supplier Delivery | 供应商配送时间 | Supply chain speed |
| Raw Material Inventory | 原材料库存 | Input stocks |
| Finished Inventory | 产成品库存 | Output stocks |
| Purchase Quantity | 采购量 | Procurement |
| Import | 进口 | Import component |
| Purchase Price | 购进价格 | Input costs |
| Factory Price | 出厂价格 | Output prices |

## Authentication Requirements

- **No authentication required** for public PMI data and news
- Some detailed reports may require CLPF membership

## Anti-Bot Measures

- Browser impersonation (chrome)
- Rate limiting (2s delay)
- Respectful crawling

## Known Limitations

1. Site structure may change, requiring selector updates
2. PMI data is released monthly (usually 1st of each month)
3. Some detailed reports require membership

## Data Quality

- **Reliability**: Very High (official PMI publisher for China)
- **Timeliness**: Monthly releases
- **Completeness**: Comprehensive logistics and PMI coverage
