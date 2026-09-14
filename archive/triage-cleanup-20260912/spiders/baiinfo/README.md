# Baiinfo Spider - 百川盈孚

Scrapling spider for Baiinfo (百川盈孚).

## Data Coverage

| Data Type | Description | Categories |
|-----------|-------------|------------|
| **Prices** | Chemical product prices | Organic, inorganic, polymers, coatings, specialty |
| **Market** | Supply/demand, inventory, analysis | All products |
| **News** | Industry, company, policy | All |

## Target URL

- **Primary**: `https://www.baiinfo.com/`

## Authentication

**No authentication required** for public data. Detailed price data and in-depth reports require paid subscription.

## Anti-Bot Measures

- Chrome browser impersonation
- Stealthy headers
- 2-second download delay

## Quick Start

```bash
pip install "scrapling[all]>=0.4.7"
scrapling install --force

cd spiders/baiinfo
python spider.py
```

## Usage

```python
from spider import get_baiinfo_data

results = get_baiinfo_data(
    include_market=True,
    include_news=True,
)
```

## Product Categories

| Category | Chinese | Products |
|----------|---------|----------|
| Organic | 有机化工 | 甲醇, 乙二醇, 苯乙烯, 丙烯腈, PTA, PVC, 纯苯, 环氧丙烷 |
| Inorganic | 无机化工 | 烧碱, 纯碱, 硫酸, 盐酸, 液氯, 钛白粉 |
| Polymers | 合成材料 | 聚乙烯, 聚丙烯, 聚苯乙烯, ABS, 聚碳酸酯, 聚甲醛 |
| Coatings | 涂料原料 | 环氧树脂, 不饱和树脂, 丙烯酸, 醇酸树脂 |
| Specialty | 特种化工 | 有机硅, 环氧氯丙烷, BDO, DMF, TDI, MDI |

## Output

### SQLite Database

`data/baiinfo.db` with tables:

| Table | Description |
|-------|-------------|
| `price_data` | Product prices with changes and regional data |
| `market_analysis` | Supply/demand, inventory, operating rates, analysis text |
| `industry_news` | News articles with related products |

### JSON Files

- `output/baiinfo_prices.json`
- `output/baiinfo_market.json`
- `output/baiinfo_news.json`

## Rate Limiting

- **Download delay**: 2 seconds
- **Concurrent requests**: 1
