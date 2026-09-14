# CCIN Spider - 中国化工信息中心

Scrapling spider for the China Chemical Information Center (中国化工信息中心).

## Data Coverage

| Data Type | Description | Categories |
|-----------|-------------|------------|
| **Prices** | Daily chemical product prices | Organic, inorganic, polymers, intermediates, fertilizers |
| **Market** | Supply/demand, inventory, operating rates | All products |
| **Standards** | GB/HG/SH technical standards | National, industry |
| **Reports** | Weekly, monthly, annual analysis | By product |
| **News** | Industry, company, policy | All |

## Target URL

- **Primary**: `http://www.ccin.com.cn/`

## Authentication

**No authentication required** for public data. Some detailed data may require subscription.

## Anti-Bot Measures

- Chrome browser impersonation
- Stealthy headers
- 3-second download delay

## Quick Start

```bash
pip install "scrapling[all]>=0.4.7"
scrapling install --force

cd spiders/ccin
python spider.py
```

## Usage

```python
from spider import get_ccin_data

results = get_ccin_data(
    include_standards=True,
    include_reports=True,
    include_news=True,
)
```

## Chemical Product Categories

| Category | Chinese | Products |
|----------|---------|----------|
| Organic | 有机化工 | 甲醇, 乙二醇, 苯乙烯, 丙烯腈, PTA, PVC |
| Inorganic | 无机化工 | 烧碱, 纯碱, 硫酸, 盐酸, 磷酸 |
| Polymers | 合成材料 | 聚乙烯, 聚丙烯, 聚苯乙烯, ABS, 聚碳酸酯 |
| Intermediates | 化工中间体 | 苯, 甲苯, 二甲苯, 萘, 蒽 |
| Fertilizers | 化肥 | 尿素, 磷酸二铵, 氯化钾, 复合肥 |

## Output

### SQLite Database

`data/ccin.db` with tables:

| Table | Description |
|-------|-------------|
| `price_data` | Product prices with changes |
| `market_analysis` | Supply/demand, inventory, operating rates |
| `industry_news` | News articles with related products |
| `technical_standards` | GB/HG/SH standards |
| `industry_reports` | Weekly/monthly/annual reports |

### JSON Files

- `output/ccin_prices.json`
- `output/ccin_market.json`
- `output/ccin_standards.json`
- `output/ccin_reports.json`
- `output/ccin_news.json`

## Rate Limiting

- **Download delay**: 3 seconds
- **Concurrent requests**: 1
