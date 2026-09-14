# Zhuochuang Spider - 卓创资讯

Scrapling spider for Zhuochuang Information (卓创资讯).

## Data Coverage

| Data Type | Description | Categories |
|-----------|-------------|------------|
| **Prices** | Chemical product prices | Energy, organic, inorganic, polymers, rubber |
| **Market** | Supply/demand, inventory, capacity | All products |
| **Index** | Price indices and commodity indices | All categories |
| **Reports** | Daily, weekly, monthly analysis | By product |

## Target URL

- **Primary**: `https://www.sci99.com/`

## Authentication

**No authentication required** for public data. Detailed price data and in-depth analysis reports require paid subscription.

## Anti-Bot Measures

- Chrome browser impersonation
- Stealthy headers
- 2-second download delay

## Quick Start

```bash
pip install "scrapling[all]>=0.4.7"
scrapling install --force

cd spiders/zhuochuang
python spider.py
```

## Usage

```python
from spider import get_zhuochuang_data

results = get_zhuochuang_data(
    include_market=True,
    include_index=True,
    include_reports=True,
)
```

## Product Categories

| Category | Chinese | Products |
|----------|---------|----------|
| Energy | 能源 | 原油, 天然气, LNG, 成品油, 燃料油, 石油焦 |
| Organic | 有机化工 | 甲醇, 乙二醇, 苯乙烯, 丙烯腈, PTA, PVC, 纯苯 |
| Inorganic | 无机化工 | 烧碱, 纯碱, 硫酸, 盐酸, 液氯 |
| Polymers | 合成材料 | 聚乙烯, 聚丙烯, 聚苯乙烯, ABS, EVA |
| Rubber | 橡胶 | 天然橡胶, 丁苯橡胶, 顺丁橡胶, 丁基橡胶 |

## Output

### SQLite Database

`data/zhuochuang.db` with tables:

| Table | Description |
|-------|-------------|
| `price_data` | Product prices with changes and regional data |
| `market_analysis` | Supply/demand, inventory, capacity, operating rates |
| `price_index` | Price and commodity indices |
| `industry_reports` | Daily/weekly/monthly reports |

### JSON Files

- `output/zhuochuang_prices.json`
- `output/zhuochuang_market.json`
- `output/zhuochuang_index.json`
- `output/zhuochuang_reports.json`

## Rate Limiting

- **Download delay**: 2 seconds
- **Concurrent requests**: 1
