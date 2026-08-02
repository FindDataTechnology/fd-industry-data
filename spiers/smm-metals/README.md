# SMM Metals Spider - 上海有色金属网数据爬虫

Specialized spider for extracting non-ferrous metal pricing data from Shanghai Metals Market (SMM).

## Data Sources

- **Primary Source**: https://www.smm.cn
- **Data Type**: Daily spot prices, market analysis
- **Update Frequency**: Daily (Chinese business hours)
- **Coverage**: Copper, Aluminum, Zinc, Lead, Nickel, Tin

## Features

- Extracts daily spot prices (low, high, average)
- Tracks price changes and percentage movements
- Handles Chinese currency (CNY/元/吨)
- Browser impersonation for anti-bot bypass
- Rate limiting (2s delay between requests)
- SQLite storage + JSON export

## Installation

```bash
cd fd-industry-data
uv sync
```

## Usage

### Python API

```python
from spiers.smm_metals.spider import get_smm_prices

# Fetch all metals
results = get_smm_prices()

# Fetch specific metals
results = get_smm_prices(["copper", "aluminum"])

# Results structure
for item in results:
    print(f"{item['date']} | {item['metal_cn']} | {item['price_avg']} {item['unit']}")
```

### Command Line

```bash
cd spiers/smm-metals
python spider.py
```

## Output

### SQLite Database (`data/smm_metals.db`)

**Table: `metal_prices`**
- `date`: Trading date
- `metal`: Metal name (English)
- `metal_cn`: Metal name (Chinese)
- `symbol`: Metal symbol (CU/AL/ZN/PB/NI/SN)
- `price_low`: Daily low price
- `price_high`: Daily high price
- `price_avg`: Daily average price
- `price_change`: Absolute price change
- `price_change_pct`: Percentage change
- `unit`: Price unit (元/吨)
- `currency`: Currency code (CNY)
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

### JSON Export (`output/smm_prices.json`)

Clean JSON array of all extracted records.

## Metal Coverage

| Metal | Chinese | Symbol | Unit |
|-------|---------|--------|------|
| Copper | 铜 | CU | 元/吨 |
| Aluminum | 铝 | AL | 元/吨 |
| Zinc | 锌 | ZN | 元/吨 |
| Lead | 铅 | PB | 元/吨 |
| Nickel | 镍 | NI | 元/吨 |
| Tin | 锡 | SN | 元/吨 |

## Anti-Bot Measures

SMM uses basic anti-bot protections:
- Browser impersonation (chrome via curl_cffi)
- Proper Referer and Accept headers
- Rate limiting (2s delay)
- Timestamp cache-busting

## Known Limitations

1. **JavaScript Rendering**: Some SMM pages require full JavaScript rendering. This spider uses browser impersonation but may miss dynamically loaded content.

2. **Login Required**: Historical data or premium reports may require SMM account login.

3. **Rate Limits**: Aggressive scraping may trigger IP blocks. Respect rate limits.

4. **Data Availability**: Prices are available during Chinese business hours (9:00-17:00 CST).

## Troubleshooting

### No data extracted
- Check if SMM is accessible: `curl -I https://www.smm.cn`
- Verify HTML structure hasn't changed
- Check logs for HTTP errors

### Cloudflare/anti-bot blocks
- Increase `time.sleep()` delay
- Rotate user agents
- Use proxy rotation (not implemented)

## Data Quality

- **Reliability**: High (official market source)
- **Timeliness**: Daily updates during trading hours
- **Completeness**: Covers all major non-ferrous metals
- **Accuracy**: Official spot prices from SMM

## License

Data sourced from Shanghai Metals Market (SMM). For commercial use, verify licensing terms with SMM directly.
