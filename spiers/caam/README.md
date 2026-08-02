# CAAM Spider - 中国汽车工业协会数据爬虫

Specialized spider for extracting automobile production/sales statistics, brand rankings, and industry news from China Association of Automobile Manufacturers.

## Data Sources

- **Primary Source**: http://www.caam.org.cn/
- **Data Type**: Production/sales statistics, brand rankings, trade data, industry analysis
- **Update Frequency**: Monthly
- **Focus**: Vehicle production, sales by type/brand, NEV data, export/import

## Features

- Extracts production statistics by vehicle type
- Scrapes sales data with brand rankings
- Collects industry news and analysis
- Vehicle type identification (passenger/commercial/NEV)
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
from spiers.caam.spider import get_caam_data

results = get_caam_data()
results = get_caam_data(include_production=True, include_sales=True, include_news=False)

production = results["production"]
sales = results["sales"]
news = results["news"]
```

### Command Line

```bash
cd spiers/caam
python spider.py
```

## Output

### SQLite Database (`data/caam.db`)

**Table: `production_statistics`**
- `period`, `vehicle_type`, `vehicle_type_cn`, `production_volume`, `production_unit`
- `growth_rate_yoy`, `growth_rate_mom`, `brand`, `category`, `source_url`, `scraped_at`

**Table: `sales_statistics`**
- `period`, `vehicle_type`, `vehicle_type_cn`, `sales_volume`, `sales_unit`
- `growth_rate_yoy`, `growth_rate_mom`, `brand`, `ranking`, `category`, `source_url`, `scraped_at`

**Table: `industry_news`**
- `date`, `title`, `content`, `category`, `source_url`, `scraped_at`

### JSON Exports

- `output/caam_production.json`
- `output/caam_sales.json`
- `output/caam_news.json`

## Vehicle Type Coverage

| Type | Chinese | Category |
|------|---------|----------|
| Passenger Vehicle | 乘用车 | Passenger |
| Commercial Vehicle | 商用车 | Commercial |
| NEV | 新能源汽车 | New Energy |
| Sedan | 轿车 | Passenger |
| SUV | SUV | Passenger |
| MPV | MPV | Passenger |
| Bus | 客车 | Commercial |
| Truck | 货车 | Commercial |

## Authentication Requirements

- **No authentication required** for public statistics and news
- Some detailed reports may require CAAM membership

## Anti-Bot Measures

- Browser impersonation (chrome)
- Proper headers
- Rate limiting (2s delay)
- Respectful crawling

## Known Limitations

1. Site may have intermittent availability
2. HTML structure may change, requiring selector updates
3. Statistics are updated monthly, not daily
4. Some premium reports require membership

## Data Quality

- **Reliability**: High (official industry association)
- **Timeliness**: Monthly updates
- **Completeness**: Comprehensive auto industry coverage
- **Accuracy**: Official statistics
