# CNIA Spider - 中国有色金属工业协会

Scrapling spider for the China Non-ferrous Metals Industry Association (中国有色金属工业协会).

## Data Coverage

| Data Type | Description | Metals |
|-----------|-------------|--------|
| **Production Stats** | Monthly/annual output statistics | Cu, Al, Zn, Pb, Ni, Sn |
| **Trade Data** | Import/export volumes and values | All 6 metals |
| **Price Data** | Daily/weekly price trends | All 6 metals |
| **Industry Reports** | Monthly, annual, industry analysis | All |
| **Industry News** | Policy updates, industry news | All |

## Target URL

- **Primary**: `https://www.chinametal.org.cn/`
- **Note**: This is the industry association site, NOT chinametal.com.cn (market network)

## Authentication

**No authentication required** for public data. Some detailed reports may require association membership.

## Anti-Bot Measures

- Chrome browser impersonation via `curl_cffi`
- Stealthy headers
- 3-second download delay
- Optional `StealthyFetcher` mode for Cloudflare bypass (`use_stealthy=True`)

## Quick Start

```bash
pip install "scrapling[all]>=0.4.7"
scrapling install --force

cd spiders/cnia
python spider.py
```

## Usage

### Fetch All Data

```python
from spider import get_cnia_data

results = get_cnia_data(
    metals=["copper", "aluminum", "zinc"],
    include_reports=True,
    include_news=True,
)
```

### Use Stealthy Mode

```python
results = get_cnia_data(
    metals=["copper", "aluminum"],
    use_stealthy=True,  # Bypass Cloudflare
)
```

## Output

### SQLite Database

`data/cnia.db` with tables:

| Table | Description |
|-------|-------------|
| `production_stats` | Production volumes by metal and period |
| `trade_data` | Import/export data |
| `price_data` | Price records |
| `industry_reports` | Report metadata |
| `industry_news` | News articles |

### JSON Files

- `output/cnia_production.json`
- `output/cnia_trade.json`
- `output/cnia_prices.json`
- `output/cnia_reports.json`
- `output/cnia_news.json`

## Metal Coverage

| Metal | English | Chinese | Symbol |
|-------|---------|---------|--------|
| Copper | copper | 铜 | Cu |
| Aluminum | aluminum | 铝 | Al |
| Zinc | zinc | 锌 | Zn |
| Lead | lead | 铅 | Pb |
| Nickel | nickel | 镍 | Ni |
| Tin | tin | 锡 | Sn |

## Rate Limiting

- **Download delay**: 3 seconds between requests
- **Concurrent requests**: 1
- Industry association servers have limited capacity
