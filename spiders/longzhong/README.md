# Longzhong Spider - 隆众资讯

Scrapling spider for Longzhong Information (隆众资讯).

## Data Coverage

| Data Type | Description | Categories |
|-----------|-------------|------------|
| **Prices** | Petrochemical product prices | Oil, gas, petrochemical, organic, polymers, rubber, fiber |
| **Market** | Supply/demand, inventory, trade data | All products |
| **Reports** | Daily, weekly, monthly analysis | By product |

## Target URL

- **Primary**: `https://www.oilchem.net/`

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

cd spiders/longzhong
python spider.py
```

## Usage

```python
from spider import get_longzhong_data

results = get_longzhong_data(
    include_market=True,
    include_trade=True,
    include_reports=True,
)
```

## Product Categories

| Category | Chinese | Products |
|----------|---------|----------|
| Oil | 原油 | WTI原油, Brent原油, 迪拜原油, 胜利原油 |
| Gas | 天然气 | LNG, PNG, CNG, 管道气 |
| Petrochemical | 石油化工 | 石脑油, 乙烯, 丙烯, 丁二烯, 纯苯, 甲苯, 二甲苯 |
| Organic | 有机化工 | 甲醇, 乙二醇, 苯乙烯, PTA, PVC, 丙烯腈 |
| Polymers | 合成树脂 | 聚乙烯, 聚丙烯, 聚苯乙烯, ABS, EVA |
| Rubber | 合成橡胶 | 丁苯橡胶, 顺丁橡胶, 丁基橡胶, 乙丙橡胶 |
| Fiber | 化纤 | 涤纶, 锦纶, 氨纶, 粘胶, 腈纶 |

## Output

### SQLite Database

`data/longzhong.db` with tables:

| Table | Description |
|-------|-------------|
| `price_data` | Product prices with changes and regional data |
| `market_data` | Supply/demand, inventory, trade volumes |
| `industry_reports` | Daily/weekly/monthly reports |

### JSON Files

- `output/longzhong_prices.json`
- `output/longzhong_market.json`
- `output/longzhong_reports.json`

## Rate Limiting

- **Download delay**: 2 seconds
- **Concurrent requests**: 1
