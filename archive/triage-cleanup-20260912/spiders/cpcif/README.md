# CPCIF Spider - 中国石油和化学工业联合会

Scrapling spider for the China Petroleum & Chemical Industry Federation (中国石油和化学工业联合会).

## Data Coverage

| Data Type | Description | Categories |
|-----------|-------------|------------|
| **Production** | Chemical output statistics | Basic chemicals, petrochemicals, fine chemicals, fertilizers, polymers |
| **Market** | Prices, supply/demand | All chemical products |
| **Trade** | Import/export data | All chemical products |
| **Reports** | Monthly, annual, industry | All |
| **News** | Industry, policy | All |

## Target URL

- **Primary**: `http://www.cpcifa.org.cn/`

## Authentication

**No authentication required** for public data. Some detailed reports may require membership.

## Anti-Bot Measures

- Chrome browser impersonation
- Stealthy headers
- 3-second download delay

## Quick Start

```bash
pip install "scrapling[all]>=0.4.7"
scrapling install --force

cd spiders/cpcif
python spider.py
```

## Usage

```python
from spider import get_cpcif_data

results = get_cpcif_data(
    include_reports=True,
    include_news=True,
)
```

## Chemical Categories

| Category | Chinese | Products |
|----------|---------|----------|
| Basic Chemicals | 基础化学品 | 硫酸, 烧碱, 纯碱, 合成氨, 尿素, 乙烯, 丙烯 |
| Petrochemicals | 石油化工 | 原油加工, 汽油, 柴油, 煤油, 润滑油, 石脑油 |
| Fine Chemicals | 精细化工 | 涂料, 染料, 颜料, 催化剂, 助剂 |
| Fertilizers | 化肥 | 氮肥, 磷肥, 钾肥, 复合肥 |
| Polymers | 合成材料 | 合成树脂, 合成纤维, 合成橡胶 |

## Output

### SQLite Database

`data/cpcif.db` with tables:

| Table | Description |
|-------|-------------|
| `production_data` | Production volumes by product |
| `market_analysis` | Price and market data |
| `trade_data` | Import/export data |
| `industry_reports` | Report metadata |
| `industry_news` | News articles |

### JSON Files

- `output/cpcif_production.json`
- `output/cpcif_market.json`
- `output/cpcif_trade.json`
- `output/cpcif_reports.json`
- `output/cpcif_news.json`

## Rate Limiting

- **Download delay**: 3 seconds
- **Concurrent requests**: 1
