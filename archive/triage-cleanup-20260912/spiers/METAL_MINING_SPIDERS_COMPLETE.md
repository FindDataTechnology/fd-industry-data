# Non-Ferrous Metal & Mining Data Spiders - Complete Implementation

Complete spider templates for extracting metal pricing, market data, and industry statistics from 4 major sources.

## Overview

This implementation provides specialized spiders for non-ferrous metal and mining data extraction:

| Spider | Source | Data Type | Coverage | Status |
|--------|--------|-----------|----------|--------|
| **SMM Metals** | Shanghai Metals Market | Daily spot prices | Chinese market | ✅ Ready |
| **CNIA-LME** | London Metal Exchange | Futures settlements | International | ✅ Ready |
| **China Metal** | China Metal Market Network | Prices + news + analysis | Chinese market | ✅ Ready |
| **CNIA** | China Non-ferrous Metal Industry Assoc | Production stats + reports | Industry-wide | ✅ Ready |

## Quick Start

### 1. Install Dependencies

```bash
cd fd-industry-data
uv sync
```

### 2. Run Individual Spiders

```bash
# SMM - Chinese spot prices
cd spiers/smm-metals
python spider.py

# LME - International futures
cd spiers/cnia-lme
python spider.py

# China Metal - Market data + news
cd spiers/chinametal
python spider.py

# CNIA - Industry statistics
cd spiers/cnia
python spider.py
```

### 3. Python API Usage

```python
from spiers.smm_metals.spider import get_smm_prices
from spiers.cnia_lme.spider import get_lme_prices
from spiers.chinametal.spider import get_chinametal_data
from spiers.cnia.spider import get_cnia_data

# Fetch all data sources
smm_data = get_smm_prices(["copper", "aluminum"])
lme_data = get_lme_prices(["copper", "aluminum"])
chinametal_data = get_chinametal_data(["copper", "aluminum"])
cnia_data = get_cnia_data()
```

## Spider Details

### 1. SMM Metals Spider (上海有色金属网)

**Location**: `spiers/smm-metals/`

**Data Extracted**:
- Daily spot prices (low, high, average)
- Price changes and percentages
- Market analysis (limited)

**Metals Covered**: Copper, Aluminum, Zinc, Lead, Nickel, Tin

**Output**:
- SQLite: `data/smm_metals.db`
- JSON: `output/smm_prices.json`

**Special Features**:
- Browser impersonation for anti-bot bypass
- Chinese currency (CNY/元/吨)
- Rate limiting (2s delay)

**Limitations**:
- Some pages require JavaScript rendering
- Historical data may need login

---

### 2. CNIA-LME Spider (伦敦金属交易所)

**Location**: `spiers/cnia-lme/`

**Data Extracted**:
- LME settlement prices (USD/tonne)
- Bid/offer spreads
- Contract months
- Currency conversion (USD ↔ CNY)

**Metals Covered**: Copper, Aluminum, Zinc, Lead, Nickel, Tin

**Output**:
- SQLite: `data/lme_metals.db`
- JSON: `output/lme_prices.json`

**Special Features**:
- Cloudflare bypass attempts
- Automatic currency conversion
- Contract month identification

**IMPORTANT Limitations**:
- ⚠️ Real-time data requires paid subscription
- ⚠️ Free data delayed 15-30 minutes
- ⚠️ Some endpoints require API key
- ⚠️ Strong Cloudflare protection may block scraping

**Recommendation**: Use SMM for Chinese market data instead.

---

### 3. China Metal Market Spider (中国金属市场网)

**Location**: `spiers/chinametal/`

**Data Extracted**:
- Daily metal prices
- Market news articles
- Industry analysis reports
- Metal-specific content identification

**Metals Covered**: Copper, Aluminum, Zinc, Lead, Nickel, Tin, Steel

**Output**:
- SQLite: `data/chinametal.db`
- JSON: `output/chinametal_prices.json`
- JSON: `output/chinametal_news.json`

**Special Features**:
- Comprehensive market coverage
- News and analysis extraction
- Bilingual support
- Category classification

**Limitations**:
- Site availability may be intermittent
- Premium content may require subscription

---

### 4. CNIA Spider (中国有色金属工业协会)

**Location**: `spiers/cnia/`

**Data Extracted**:
- Production statistics (volume, growth rates)
- Industry news and policy documents
- Analysis reports (annual, monthly)
- Regional breakdowns

**Metals Covered**: Copper, Aluminum, Zinc, Lead, Nickel, Tin

**Output**:
- SQLite: `data/cnia.db`
- JSON: `output/cnia_statistics.json`
- JSON: `output/cnia_news.json`
- JSON: `output/cnia_reports.json`

**Special Features**:
- Official industry association data
- Production volume tracking
- Policy document extraction
- Report categorization

**Limitations**:
- Monthly/quarterly updates (not daily)
- Some reports may require membership

---

## Data Schema Comparison

### Price Data

| Field | SMM | LME | China Metal |
|-------|-----|-----|-------------|
| date | ✅ | ✅ | ✅ |
| metal | ✅ | ✅ | ✅ |
| price_low | ✅ | ❌ | ✅ |
| price_high | ✅ | ❌ | ✅ |
| price_avg | ✅ | ❌ | ✅ |
| settlement_price | ❌ | ✅ | ❌ |
| bid_price | ❌ | ✅ | ❌ |
| offer_price | ❌ | ✅ | ❌ |
| price_change | ✅ | ✅ | ✅ |
| currency | CNY | USD | CNY |
| unit | 元/吨 | USD/tonne | 元/吨 |

### News & Analysis

| Field | China Metal | CNIA |
|-------|-------------|------|
| date | ✅ | ✅ |
| title | ✅ | ✅ |
| content | ✅ | ✅ |
| category | ✅ | ✅ |
| metal | ✅ | ✅ |
| report_type | ❌ | ✅ |
| author | ❌ | ✅ |

## Recommended Usage

### For Daily Market Prices

1. **Primary**: SMM (Chinese market)
   ```python
   from spiers.smm_metals.spider import get_smm_prices
   prices = get_smm_prices()
   ```

2. **Secondary**: China Metal (with news context)
   ```python
   from spiers.chinametal.spider import get_chinametal_data
   data = get_chinametal_data()
   ```

### For International Futures

**LME** (with caveats):
```python
from spiers.cnia_lme.spider import get_lme_prices
prices = get_lme_prices()
```

**Note**: Consider alternative sources if LME access is problematic.

### For Industry Statistics

**CNIA** (production data):
```python
from spiers.cnia.spider import get_cnia_data
stats = get_cnia_data()
```

### For Comprehensive Analysis

Combine all sources:
```python
# Daily prices
smm_prices = get_smm_prices()
chinametal_data = get_chinametal_data()

# Industry context
cnia_data = get_cnia_data()

# International reference
lme_prices = get_lme_prices()
```

## Anti-Bot Strategies

All spiders implement:
- Browser impersonation (chrome via curl_cffi)
- Proper headers (Referer, Accept, User-Agent)
- Rate limiting (2-3s delays)
- Respectful crawling

**Additional measures for LME**:
- Cloudflare bypass attempts
- May require proxy rotation for production use

## Data Quality Assessment

| Source | Reliability | Timeliness | Completeness | Access |
|--------|-------------|------------|--------------|--------|
| SMM | ⭐⭐⭐⭐⭐ | Daily | High | Free (basic) |
| LME | ⭐⭐⭐⭐⭐ | Delayed | High | Subscription |
| China Metal | ⭐⭐⭐⭐ | Daily | Medium-High | Free (basic) |
| CNIA | ⭐⭐⭐⭐⭐ | Monthly | High | Free |

## File Structure

```
fd-industry-data/spiers/
├── smm-metals/
│   ├── spider.py
│   ├── manifest.yaml
│   ├── README.md
│   ├── data/
│   └── output/
├── cnia-lme/
│   ├── spider.py
│   ├── manifest.yaml
│   ├── README.md
│   ├── data/
│   └── output/
├── chinametal/
│   ├── spider.py
│   ├── manifest.yaml
│   ├── README.md
│   ├── data/
│   └── output/
└── cnia/
    ├── spider.py
    ├── manifest.yaml
    ├── README.md
    ├── data/
    └── output/
```

## Known Issues & Troubleshooting

### Common Issues

1. **No data extracted**
   - Check site accessibility: `curl -I <url>`
   - Verify HTML structure hasn't changed
   - Check logs for HTTP errors

2. **Cloudflare/anti-bot blocks**
   - Increase delay between requests
   - Use residential proxy
   - Consider headless browser (Playwright/Selenium)

3. **LME access problems**
   - LME has strong protection
   - Consider SMM for Chinese market instead
   - May need paid subscription for reliable access

### Debugging

Enable debug logging:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

Check extracted HTML:
```python
from scrapling import Fetcher
fetcher = Fetcher(impersonate="chrome")
response = fetcher.get("https://www.smm.cn/prices/copper")
print(response.html[:1000])
```

## Future Enhancements

Potential improvements:
1. **Proxy rotation** for production scraping
2. **Headless browser** integration (Playwright)
3. **Scheduled crawling** with cron/airflow
4. **Data validation** and quality checks
5. **Alert system** for scraping failures
6. **Additional sources**: Fastmarkets, Metal Bulletin, Kitco

## License

Data sourced from respective platforms. For commercial use, verify licensing terms with each data provider directly.

## Support

For issues or questions:
- Check individual spider README files
- Review manifest.yaml for data schema
- Inspect logs for error details

---

**Implementation Date**: 2026-07-31  
**Total Spiders**: 4  
**Total Data Sources**: 4  
**Metals Covered**: 6 (Cu, Al, Zn, Pb, Ni, Sn)  
**Data Types**: Prices, News, Analysis, Statistics, Reports
