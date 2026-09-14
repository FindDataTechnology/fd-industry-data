# CNMIA Spider - 中国有色金属工业协会

Scrapling spider for China Nonferrous Metals Industry Association (中国有色金属工业协会).

## Data Coverage

| Data Type | Description | Categories |
|-----------|-------------|------------|
| **Production** | Monthly/annual production statistics | Copper, aluminum, zinc, lead, nickel, tin |
| **Trade** | Import/export volumes and values | All metals |
| **Prices** | Domestic, international, index prices | All metals |
| **Reports** | Monthly, annual, analysis reports | By metal |
| **News** | Industry and policy news | All |

## Target URL

- **Primary**: `https://www.chinametal.org.cn/`

## Authentication

**No authentication required** for statistical data. Some detailed data may require association membership.

## Anti-Bot Measures

- Chrome browser impersonation
- Stealthy headers
- 2-second download delay

## Quick Start

```bash
pip install "scrapling[all]>=0.4.7"
scrapling install --force

cd spiders/cnmia
python spider.py
```

## Usage

```python
from spider import get_cnmia_data

results = get_cnmia_data(
    include_production=True,
    include_trade=True,
    include_prices=True,
    include_reports=True,
    include_news=True,
)
```

## Metal Categories

| Category | Chinese | Products |
|----------|---------|----------|
| Copper | 铜 | 电解铜, 铜精矿, 铜材, 废铜 |
| Aluminum | 铝 | 电解铝, 氧化铝, 铝锭, 铝合金, 废铝 |
| Zinc | 锌 | 锌锭, 锌精矿, 氧化锌 |
| Lead | 铅 | 铅锭, 铅精矿 |
| Nickel | 镍 | 电解镍, 镍矿, 镍铁 |
| Tin | 锡 | 锡锭, 锡精矿 |
| Rare Earth | 稀土 | 氧化镨钕, 氧化镝, 氧化铽, 稀土永磁 |
| Precious | 贵金属 | 黄金, 白银, 铂金, 钯金 |

## Output

### SQLite Database

`data/cnmia.db` with tables:

| Table | Description |
|-------|-------------|
| `production_data` | Production statistics with YoY changes |
| `trade_data` | Import/export volumes and values |
| `price_data` | Domestic, international, and index prices |
| `industry_reports` | Monthly/annual/analysis reports |
| `industry_news` | News articles with related metals |

### JSON Files

- `output/cnmia_production.json`
- `output/cnmia_trade.json`
- `output/cnmia_prices.json`
- `output/cnmia_reports.json`
- `output/cnmia_news.json`

## Rate Limiting

- **Download delay**: 2 seconds
- **Concurrent requests**: 1
