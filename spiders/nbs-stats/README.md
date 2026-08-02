# NBS Stats Spider - 国家统计局综合数据

Scrapling spider for comprehensive data from the National Bureau of Statistics of China (国家统计局).

## Data Coverage

| Category | Indicators | Frequency |
|----------|-----------|-----------|
| **GDP** | Quarterly, Annual, By Province | Q/A |
| **Price Indices** | CPI, PPI | Monthly |
| **Industrial** | Production, PMI | Monthly |
| **Population** | Annual population | Annual |
| **Employment** | Urban employment | Monthly |

## Data Sources

- **Primary**: `https://data.stats.gov.cn/easyquery.htm` (NBS API)
- **Fallback**: akshare library (wraps NBS data)

## Authentication

**No authentication required.** All data is publicly available through the NBS easyquery API.

## Anti-Bot Measures

- Chrome browser impersonation via `curl_cffi`
- Proper `Referer` and `X-Requested-With` headers
- 2-second download delay
- Timestamp cache-busting (`k1` parameter)

## Quick Start

```bash
# Install dependencies
pip install "scrapling[all]>=0.4.7"
scrapling install --force

# Run the spider
cd spiders/nbs-stats
python spider.py
```

## Usage

### Fetch All Categories

```python
import asyncio
from spider import get_nbs_data

results = asyncio.run(get_nbs_data(
    categories=["gdp", "price_indices", "industrial", "population", "employment"],
    start_year=2015,
))
```

### Fetch Specific Category

```python
from spider import get_gdp_data, get_price_indices

gdp = get_gdp_data(start_year=2020)
cpi_ppi = get_price_indices(start_year=2020)
```

### Using the Spider Class

```python
from spider import NbsStatsSpider

spider = NbsStatsSpider()
spider.categories = ["gdp", "price_indices"]
spider.start_year = 2015
result = spider.start()
result.items.to_json("nbs_data.json")
```

## Output

### SQLite Database

`data/nbs_stats.db` with table `nbs_indicators`:

| Column | Type | Description |
|--------|------|-------------|
| period | TEXT | 统计期间 (e.g., 2024Q1, 2024-01, 2024) |
| category | TEXT | 数据类别 |
| indicator_type | TEXT | 指标类型 |
| value | REAL | 指标数值 |
| indicator_code | TEXT | NBS内部编码 |
| indicator_name | TEXT | 指标名称 |
| unit | TEXT | 单位 |
| frequency | TEXT | 频率 (Q/A/M) |
| source | TEXT | 数据来源 |
| fetched_at | TEXT | 抓取时间 |

### JSON Files

- `output/nbs_gdp.json` - GDP data
- `output/nbs_price_indices.json` - CPI/PPI data
- `output/nbs_industrial.json` - Industrial production & PMI
- `output/nbs_population.json` - Population data
- `output/nbs_employment.json` - Employment data

## Rate Limiting

- **Download delay**: 2 seconds between requests
- **Concurrent requests**: 1
- **Recommended**: Do not reduce delay below 2s

## Notes

- NBS API returns data in a custom JSON format with `returncode`, `returndata`, `datanodes`, and `wdnodes`
- Period parsing handles Chinese date formats (e.g., "2024年第1季度" → "2024Q1")
- akshare fallback is used when direct API calls fail
- Data is deduplicated via SQLite `UNIQUE` constraints
