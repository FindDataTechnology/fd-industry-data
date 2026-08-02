# China Railway Spider - 中国国家铁路集团数据爬虫

Specialized spider for extracting railway freight, passenger, infrastructure, and investment data from China Railway Corporation.

## Data Sources

- **Primary Source**: http://www.china-railway.com.cn/
- **Data Type**: Railway freight data, passenger volume, infrastructure statistics, investment data
- **Update Frequency**: Monthly
- **Focus**: National railway transport indicators, freight tonnage, HSR data, investment figures

## Features

- Railway freight volume and turnover extraction
- Passenger dispatch and volume tracking
- Fixed asset investment data (infrastructure + equipment)
- Railway network mileage statistics (total, HSR, electrified, double-track)
- Seasonal transport data (Spring Festival, summer rush)
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
from spiers.china_railway.spider import get_china_railway_data

results = get_china_railway_data()
results = get_china_railway_data(include_freight=True, include_passenger=True, include_investment=True, include_news=False)
```

### Command Line

```bash
cd spiers/china-railway
python spider.py
```

## Output

### SQLite Database (`data/china_railway.db`)

**Table: `railway_freight`**
- `period`, `indicator`, `indicator_cn`, `value`, `unit`
- `growth_rate`, `growth_unit`, `category`
- `source_url`, `scraped_at`

**Table: `railway_passenger`**
- `period`, `indicator`, `indicator_cn`, `value`, `unit`
- `growth_rate`, `growth_unit`, `category`
- `source_url`, `scraped_at`

**Table: `railway_investment`**
- `period`, `indicator`, `indicator_cn`, `value`, `unit`
- `growth_rate`, `growth_unit`, `category`
- `source_url`, `scraped_at`

**Table: `railway_news`**
- `date`, `title`, `content`, `category`, `source_url`, `scraped_at`

### JSON Exports

- `output/railway_freight.json`
- `output/railway_passenger.json`
- `output/railway_investment.json`
- `output/railway_news.json`

## Indicator Coverage

| Indicator | Chinese | Description |
|-----------|---------|-------------|
| passenger_dispatched | 旅客发送量 | Passengers dispatched |
| freight_dispatched | 货运发送量/货物发送量 | Freight dispatched |
| freight_turnover | 货物周转量 | Freight ton-km |
| passenger_turnover | 旅客周转量 | Passenger-km |
| railway_investment | 铁路投资 | Total railway investment |
| fixed_asset_investment | 固定资产投资 | Fixed asset investment |
| infrastructure_investment | 基建投资 | Infrastructure investment |
| equipment_investment | 装备投资 | Equipment investment |
| operating_mileage | 营业里程 | Operating mileage |
| hsr_mileage | 高铁里程 | High-speed rail mileage |
| electrified_mileage | 电气化里程 | Electrified mileage |
| double_track_mileage | 复线里程 | Double-track mileage |
| daily_avg_loading | 日均装车 | Daily average wagon loading |

## Authentication Requirements

- **No authentication required** for public transport data and news
- Government/state-owned enterprise website; data is publicly available
- Some detailed statistical reports may require formal request

## Anti-Bot Measures

- Browser impersonation (chrome)
- Rate limiting (2s delay)
- Respectful crawling

## Known Limitations

1. Site may have limited public data pages; most data is in news/announcements
2. Statistical data is often embedded in text rather than structured tables
3. URL paths are estimated; actual paths may differ
4. Some data is only released in annual statistical bulletins

## Data Quality

- **Reliability**: Very High (official national railway operator)
- **Timeliness**: Monthly releases
- **Completeness**: Comprehensive railway transport coverage
