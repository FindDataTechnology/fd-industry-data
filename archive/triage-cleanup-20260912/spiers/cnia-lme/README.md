# LME Metals Spider - 伦敦金属交易所数据爬虫

Specialized spider for extracting LME (London Metal Exchange) futures and settlement prices.

## Data Sources

- **Primary Source**: https://www.lme.com
- **Data Type**: Futures settlement prices, warehouse stocks
- **Update Frequency**: Daily (London trading hours)
- **Coverage**: Copper, Aluminum, Zinc, Lead, Nickel, Tin

## IMPORTANT: Access Restrictions

LME data access has significant restrictions:

1. **Real-time Data**: Requires paid subscription
2. **Delayed Data**: 15-30 minute delay available for free
3. **API Access**: Some endpoints require registration and API key
4. **Cloudflare Protection**: Site uses advanced anti-bot measures

**Recommendation**: For Chinese market data, use SMM (Shanghai Metals Market) instead.

## Features

- Extracts LME settlement prices (USD/tonne)
- Tracks bid/offer spreads
- Currency conversion (USD ↔ CNY)
- Contract month identification
- Cloudflare bypass via browser impersonation
- Rate limiting (3s delay)
- SQLite storage + JSON export

## Installation

```bash
cd fd-industry-data
uv sync
```

## Usage

### Python API

```python
from spiers.cnia_lme.spider import get_lme_prices

# Fetch all metals
results = get_lme_prices()

# Fetch specific metals
results = get_lme_prices(["copper", "aluminum"])

# Results include USD and CNY conversions
for item in results:
    print(f"{item['date']} | {item['metal_cn']} | ${item['settlement_price']} USD/tonne")
```

### Command Line

```bash
cd spiers/cnia-lme
python spider.py
```

## Output

### SQLite Database (`data/lme_metals.db`)

**Table: `lme_prices`**
- `date`: Trading date
- `metal`: Metal name (English)
- `metal_cn`: Metal name (Chinese)
- `symbol`: Metal symbol
- `lme_code`: LME contract code (CA/AH/ZS/PB/NI/SN)
- `settlement_price`: Settlement price (USD/tonne)
- `bid_price`: Bid price
- `offer_price`: Offer price
- `price_change`: Absolute price change
- `price_change_pct`: Percentage change
- `unit`: Price unit (USD/tonne)
- `currency`: Currency code (USD)
- `contract_month`: Contract delivery month
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `exchange_rates`**
- USD/CNY exchange rates for currency conversion

### JSON Export (`output/lme_prices.json`)

Clean JSON array with both USD and CNY prices.

## Metal Coverage

| Metal | Chinese | LME Code | Unit |
|-------|---------|----------|------|
| Copper | 铜 | CA | USD/tonne |
| Aluminum | 铝 | AH | USD/tonne |
| Zinc | 锌 | ZS | USD/tonne |
| Lead | 铅 | PB | USD/tonne |
| Nickel | 镍 | NI | USD/tonne |
| Tin | 锡 | SN | USD/tonne |

## Anti-Bot Measures

LME uses Cloudflare protection:
- Browser impersonation (chrome via curl_cffi)
- Proper headers and cookies
- Rate limiting (3s delay)
- May still trigger CAPTCHA on aggressive scraping

## Known Limitations

1. **Cloudflare Blocks**: LME has strong anti-bot protection. May require:
   - Proxy rotation
   - Headless browser (Playwright/Selenium)
   - Manual CAPTCHA solving

2. **Data Delays**: Free data is delayed 15-30 minutes

3. **Subscription Required**: Real-time prices need paid LME subscription

4. **Limited Access**: Some data endpoints require API key registration

## Troubleshooting

### No data extracted
- Check if LME is accessible: `curl -I https://www.lme.com`
- May be blocked by Cloudflare
- Try alternative source (SMM for Chinese market)

### Cloudflare challenges
- Increase delay between requests
- Use residential proxy
- Consider headless browser automation

### Exchange rate fetch fails
- Spider uses fallback rate (7.0 CNY/USD)
- Can manually set rate in code

## Alternative Data Sources

If LME access is problematic:

1. **SMM (Shanghai Metals Market)**: Chinese market prices
   ```python
   from spiers.smm_metals.spider import get_smm_prices
   ```

2. **SHFE (Shanghai Futures Exchange)**: Chinese futures
   ```python
   # See existing shfe_spider.py
   ```

3. **Fastmarkets**: International prices (may also require subscription)

## Data Quality

- **Reliability**: High (official exchange source)
- **Timeliness**: Delayed 15-30 minutes (free tier)
- **Completeness**: All major base metals
- **Accuracy**: Official LME settlement prices

## License

Data sourced from London Metal Exchange (LME). For commercial use, verify licensing terms with LME directly.

## See Also

- [SMM Metals Spider](../smm-metals/README.md) - Chinese market prices
- [China Metal Spider](../chinametal/README.md) - Industry association data
