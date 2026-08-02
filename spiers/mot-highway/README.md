# MOT Highway Spider - 交通运输部公路管理数据爬虫

Specialized spider for extracting highway traffic, infrastructure, and investment data from the Ministry of Transport.

## Data Sources

- **Primary Source**: http://www.mot.gov.cn/
- **Data Type**: Highway traffic data, vehicle flow statistics, infrastructure data, investment statistics
- **Update Frequency**: Monthly
- **Focus**: National highway network, toll station data, road freight/passenger volume

## Features

- Highway passenger and freight volume extraction
- Road network mileage data (expressway, national, provincial, rural)
- Bridge and tunnel statistics
- Transport fixed asset investment tracking
- Toll highway data
- Regional breakdown for infrastructure data
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
from spiers.mot_highway.spider import get_mot_highway_data

results = get_mot_highway_data()
results = get_mot_highway_data(include_traffic=True, include_infrastructure=True, include_investment=True, include_news=False)
```

### Command Line

```bash
cd spiers/mot-highway
python spider.py
```

## Output

### SQLite Database (`data/mot_highway.db`)

**Table: `highway_traffic`**
- `period`, `indicator`, `indicator_cn`, `value`, `unit`
- `growth_rate`, `growth_unit`, `category`
- `source_url`, `scraped_at`

**Table: `highway_infrastructure`**
- `period`, `indicator`, `indicator_cn`, `value`, `unit`
- `growth_rate`, `growth_unit`, `category`, `region`
- `source_url`, `scraped_at`

**Table: `highway_investment`**
- `period`, `indicator`, `indicator_cn`, `value`, `unit`
- `growth_rate`, `growth_unit`, `category`
- `source_url`, `scraped_at`

**Table: `highway_news`**
- `date`, `title`, `content`, `category`, `source_url`, `scraped_at`

### JSON Exports

- `output/highway_traffic.json`
- `output/highway_infrastructure.json`
- `output/highway_investment.json`
- `output/highway_news.json`

## Indicator Coverage

| Indicator | Chinese | Description |
|-----------|---------|-------------|
| highway_passenger_volume | 公路客运量 | Highway passenger volume |
| highway_freight_volume | 公路货运量 | Highway freight volume |
| highway_passenger_turnover | 公路旅客周转量 | Passenger-km by road |
| highway_freight_turnover | 公路货物周转量 | Ton-km by road |
| expressway_mileage | 高速公路里程 | Expressway mileage |
| highway_mileage | 公路里程 | Total highway mileage |
| national_road_mileage | 国道里程 | National road mileage |
| provincial_road_mileage | 省道里程 | Provincial road mileage |
| rural_road_mileage | 农村公路里程 | Rural road mileage |
| bridge_count | 桥梁数量 | Number of bridges |
| tunnel_count | 隧道数量 | Number of tunnels |
| toll_station_count | 收费站数量 | Number of toll stations |
| daily_avg_traffic | 日均交通量 | Daily average traffic |
| transport_fixed_investment | 交通固定资产投资 | Transport fixed asset investment |
| highway_construction_investment | 公路建设投资 | Highway construction investment |
| highway_maintenance_investment | 公路养护投资 | Highway maintenance investment |
| motor_vehicle_ownership | 机动车保有量 | Motor vehicle ownership |

## Authentication Requirements

- **No authentication required** for public statistics and policy data
- Government website; data is publicly available
- Some detailed statistical yearbooks may require purchase

## Anti-Bot Measures

- Browser impersonation (chrome)
- Rate limiting (2s delay)
- Respectful crawling

## Known Limitations

1. Government website may have access restrictions during peak hours
2. Statistical data is released with 1-2 month lag
3. Some detailed data is only available in annual transport development bulletins
4. URL paths are estimated; actual paths may differ from the site's current structure

## Data Quality

- **Reliability**: Very High (official Ministry of Transport)
- **Timeliness**: Monthly releases with slight lag
- **Completeness**: Comprehensive highway and road transport coverage
