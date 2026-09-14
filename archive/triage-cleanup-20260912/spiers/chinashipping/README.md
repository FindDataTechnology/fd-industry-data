# China Shipping Association Spider - 中国船东协会数据爬虫

Specialized spider for extracting shipping market indices, fleet statistics, and trade route data from China Shipping Association.

## Data Sources

- **Primary Source**: http://www.chinashipping.org.cn/
- **Data Type**: Shipping industry data, fleet statistics, trade route data, market analysis
- **Update Frequency**: Weekly (market indices), Monthly (fleet/trade)
- **Focus**: CCFI/CBFI shipping indices, fleet capacity, container shipping volumes

## Features

- Shipping market index extraction (CCFI, CBFI, CIDFI)
- Route-level freight rate tracking
- Fleet statistics by vessel type (container, bulk, tanker, LNG)
- Trade route volume data
- Multiple cargo type coverage
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
from spiers.chinashipping.spider import get_chinashipping_data

results = get_chinashipping_data()
results = get_chinashipping_data(include_market=True, include_fleet=True, include_trade=True, include_news=False)
```

### Command Line

```bash
cd spiers/chinashipping
python spider.py
```

## Output

### SQLite Database (`data/chinashipping.db`)

**Table: `shipping_market`**
- `period`, `indicator`, `indicator_cn`, `value`, `unit`
- `change_value`, `change_unit`, `route`, `route_cn`
- `source_url`, `scraped_at`

**Table: `shipping_fleet`**
- `period`, `ship_type`, `ship_type_cn`, `vessel_count`, `deadweight`
- `dw_unit`, `teu_capacity`, `teu_unit`
- `growth_rate`, `growth_unit`
- `source_url`, `scraped_at`

**Table: `shipping_trade`**
- `period`, `route`, `route_cn`, `cargo_type`, `cargo_type_cn`
- `value`, `unit`, `growth_rate`, `growth_unit`
- `source_url`, `scraped_at`

**Table: `shipping_news`**
- `date`, `title`, `content`, `category`, `source_url`, `scraped_at`

### JSON Exports

- `output/shipping_market.json`
- `output/shipping_fleet.json`
- `output/shipping_trade.json`
- `output/shipping_news.json`

## Market Index Coverage

| Index | Chinese | Description |
|-------|---------|-------------|
| CCFI | 中国出口集装箱运价指数 | China Containerized Freight Index |
| CBFI | 中国沿海散货运价指数 | China Coastal Bulk Freight Index |
| CIDFI | 中国进口干散货运价指数 | China Imported Dry Bulk Freight Index |
| CICOTFI | 中国进口原油运价指数 | China Imported Crude Oil Tanker Freight Index |

## Ship Type Coverage

| Type | Chinese | Code |
|------|---------|------|
| Container Ship | 集装箱船 | container_ship |
| Bulk Carrier | 散货船 | bulk_carrier |
| Tanker | 油船 | tanker |
| LNG/LPG Carrier | 液化气船 | lng_lpg_carrier |
| Chemical Tanker | 化学品船 | chemical_tanker |
| Ro-Ro Ship | 滚装船 | ro_ro_ship |
| General Cargo | 杂货船 | general_cargo |
| Reefer Ship | 冷藏船 | reefer_ship |

## Trade Route Coverage

| Route | Chinese | Code |
|-------|---------|------|
| Far East - Europe | 远东-欧洲 | far_east_europe |
| Far East - North America | 远东-北美 | far_east_north_america |
| Far East - Mediterranean | 远东-地中海 | far_east_mediterranean |
| Far East - Persian Gulf | 远东-波斯湾 | far_east_persian_gulf |
| Far East - South America | 远东-南美 | far_east_south_america |
| China - Southeast Asia | 中国-东南亚 | china_southeast_asia |
| China - Japan/Korea | 中国-日韩 | china_japan_korea |
| China - Australia | 中国-澳洲 | china_australia |

## Authentication Requirements

- **No authentication required** for public market indices and news
- Some detailed industry analysis reports may require association membership
- CCFI data is also published on Shanghai Shipping Exchange (publicly available)

## Anti-Bot Measures

- Browser impersonation (chrome)
- Rate limiting (2s delay)
- Respectful crawling

## Known Limitations

1. Site structure may change, requiring selector updates
2. Market indices are released weekly; fleet/trade data monthly
3. Some detailed reports require membership
4. URL paths are estimated; actual paths may differ from the site's current structure

## Data Quality

- **Reliability**: High (official shipping industry association)
- **Timeliness**: Weekly for market indices, monthly for fleet/trade
- **Completeness**: Comprehensive shipping market coverage
