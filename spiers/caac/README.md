# CAAC Spider - 中国民用航空局数据爬虫

Specialized spider for extracting aviation statistics, passenger traffic, cargo volume, and route data from the Civil Aviation Administration of China.

## Data Sources

- **Primary Source**: http://www.caac.gov.cn/
- **Data Type**: Aviation statistics, passenger traffic, cargo/mail volume, route statistics
- **Update Frequency**: Monthly
- **Focus**: Airport rankings, transport turnover, flight on-time rates, aviation infrastructure

## Features

- Extracts airport passenger throughput data (top airports)
- Cargo/mail throughput by airport
- National aviation transport indicators
- Flight on-time rate tracking
- Route and mileage statistics
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
from spiers.caac.spider import get_caac_data

results = get_caac_data()
results = get_caac_data(include_passenger=True, include_cargo=True, include_stats=True, include_news=False)
```

### Command Line

```bash
cd spiers/caac
python spider.py
```

## Output

### SQLite Database (`data/caac.db`)

**Table: `aviation_passenger`**
- `period`, `airport_name`, `airport_code`, `indicator`, `indicator_cn`
- `value`, `unit`, `growth_rate`, `growth_unit`, `ranking`
- `source_url`, `scraped_at`

**Table: `aviation_cargo`**
- `period`, `airport_name`, `airport_code`, `indicator`, `indicator_cn`
- `value`, `unit`, `growth_rate`, `growth_unit`, `ranking`
- `source_url`, `scraped_at`

**Table: `aviation_statistics`**
- `period`, `indicator`, `indicator_cn`, `value`, `unit`
- `growth_rate`, `growth_unit`, `category`
- `source_url`, `scraped_at`

**Table: `aviation_news`**
- `date`, `title`, `content`, `category`, `source_url`, `scraped_at`

### JSON Exports

- `output/aviation_passenger.json`
- `output/aviation_cargo.json`
- `output/aviation_statistics.json`
- `output/aviation_news.json`

## Airport Coverage

| Airport | Chinese | Code |
|---------|---------|------|
| Beijing Capital | 首都机场/北京首都 | beijing_capital |
| Beijing Daxing | 大兴机场/北京大兴 | beijing_daxing |
| Shanghai Pudong | 浦东机场/上海浦东 | shanghai_pudong |
| Shanghai Hongqiao | 虹桥机场/上海虹桥 | shanghai_hongqiao |
| Guangzhou Baiyun | 白云机场/广州白云 | guangzhou_baiyun |
| Shenzhen Baoan | 宝安机场/深圳宝安 | shenzhen_baoan |
| Chengdu Shuangliu | 双流机场 | chengdu_shuangliu |
| Chengdu Tianfu | 天府机场 | chengdu_tianfu |
| Hangzhou Xiaoshan | 萧山机场/杭州萧山 | hangzhou_xiaoshan |
| Chongqing Jiangbei | 江北机场/重庆江北 | chongqing_jiangbei |
| Xi'an Xianyang | 咸阳机场/西安咸阳 | xian_xianyang |

## Indicator Coverage

| Indicator | Chinese | Description |
|-----------|---------|-------------|
| passenger_volume | 旅客运输量 | Total passengers transported |
| passenger_throughput | 旅客吞吐量 | Airport passenger throughput |
| cargo_mail_volume | 货邮运输量 | Cargo and mail transported |
| cargo_mail_throughput | 货邮吞吐量 | Airport cargo/mail throughput |
| flight_on_time_rate | 航班正常率 | On-time performance |
| total_transport_turnover | 运输总周转量 | Total transport turnover |
| passenger_turnover | 旅客周转量 | Passenger-km |
| aircraft_movements | 飞机起降架次 | Aircraft takeoffs/landings |
| transport_airports | 运输机场数 | Number of transport airports |
| route_count | 航线条数 | Number of routes |

## Authentication Requirements

- **No authentication required** for public statistics and news
- Government website; data is publicly available
- Some detailed statistical yearbooks may require purchase

## Anti-Bot Measures

- Browser impersonation (chrome)
- Rate limiting (2s delay)
- Respectful crawling

## Known Limitations

1. Government website may have strict access controls during peak hours
2. Statistical data is typically released monthly with 1-2 month lag
3. Some historical data may only be available in annual statistical bulletins
4. URL paths are estimated; actual paths may differ

## Data Quality

- **Reliability**: Very High (official civil aviation authority)
- **Timeliness**: Monthly releases with slight lag
- **Completeness**: Comprehensive aviation industry coverage
