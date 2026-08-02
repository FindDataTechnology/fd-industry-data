# China Ports Association Spider - 中国港口协会数据爬虫

Specialized spider for extracting port throughput, container traffic, and trade flow data from China Ports Association.

## Data Sources

- **Primary Source**: http://www.chinaports.com/
- **Data Type**: Port throughput statistics, container traffic, cargo volume, trade flow data
- **Update Frequency**: Monthly
- **Focus**: Major port throughput rankings, TEU container volumes, bulk cargo statistics

## Features

- Extracts port throughput data (total cargo, bulk cargo, container)
- Container traffic (TEU) tracking for 21 major ports
- Multiple cargo type coverage (coal, iron ore, crude oil, grain, steel)
- Port ranking extraction
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
from spiers.chinaports.spider import get_chinaports_data

results = get_chinaports_data()
results = get_chinaports_data(include_throughput=True, include_container=True, include_news=False)
```

### Command Line

```bash
cd spiers/chinaports
python spider.py
```

## Output

### SQLite Database (`data/chinaports.db`)

**Table: `port_throughput`**
- `period`, `port_name`, `port_name_en`, `cargo_type`, `cargo_type_cn`
- `value`, `unit`, `growth_rate`, `growth_unit`, `ranking`
- `source_url`, `scraped_at`

**Table: `container_traffic`**
- `period`, `port_name`, `port_name_en`, `teu_value`, `teu_unit`
- `growth_rate`, `growth_unit`, `ranking`
- `source_url`, `scraped_at`

**Table: `port_news`**
- `date`, `title`, `content`, `category`, `source_url`, `scraped_at`

### JSON Exports

- `output/port_throughput.json`
- `output/port_container.json`
- `output/port_news.json`

## Port Coverage

| Port | Chinese | Code |
|------|---------|------|
| Shanghai | 上海港 | shanghai |
| Ningbo-Zhoushan | 宁波舟山港 | ningbo_zhoushan |
| Shenzhen | 深圳港 | shenzhen |
| Guangzhou | 广州港 | guangzhou |
| Qingdao | 青岛港 | qingdao |
| Tianjin | 天津港 | tianjin |
| Xiamen | 厦门港 | xiamen |
| Dalian | 大连港 | dalian |
| Suzhou | 苏州港 | suzhou |
| Yingkou | 营口港 | yingkou |
| Tangshan | 唐山港 | tangshan |
| Rizhao | 日照港 | rizhao |
| Yantai | 烟台港 | yantai |
| Lianyungang | 连云港 | lianyungang |
| Fuzhou | 福州港 | fuzhou |
| Quanzhou | 泉州港 | quanzhou |
| Zhanjiang | 湛江港 | zhanjiang |
| Beibu Gulf | 北部湾港 | beibu_gulf |
| Chongqing | 重庆港 | chongqing |
| Nanjing | 南京港 | nanjing |
| Wuhan | 武汉港 | wuhan |

## Authentication Requirements

- **No authentication required** for public port statistics and news
- Some detailed industry reports may require association membership

## Anti-Bot Measures

- Browser impersonation (chrome)
- Rate limiting (2s delay)
- Respectful crawling

## Known Limitations

1. Site structure may change, requiring selector updates
2. Port data is released monthly
3. Some detailed reports require membership
4. URL paths are estimated; actual paths may differ from the site's current structure

## Data Quality

- **Reliability**: High (official port industry association)
- **Timeliness**: Monthly releases
- **Completeness**: Comprehensive port and cargo coverage
