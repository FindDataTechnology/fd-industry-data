# Tonghuashun iFinD Spider - 同花顺iFinD爬虫

## Overview

Scrapling-based spider for extracting public financial data from Tonghuashun iFinD (同花顺iFinD).

## Data Sources

**Target URL**: https://www.5ifin.com

### Publicly Accessible Data
- Financial news and market commentary
- Basic stock quotes (delayed)
- Fund rankings and NAV data
- Industry trend summaries
- Economic calendar

### Premium Content (Subscription Required)
- Real-time Level-2 market data
- Financial statement database
- Custom stock screening
- Analyst consensus estimates
- Institutional research database

## Authentication Requirements

**iFinD Terminal Access**:
- Requires paid iFinD subscription
- Individual license: ~¥10,000-20,000/year
- Institutional license: Custom pricing
- API access requires separate data API license

**Public Website**:
- No authentication needed for news and basic data
- Good coverage of fund rankings
- Delayed stock quotes (15-30 minutes)

## Installation

```bash
cd fd-industry-data
uv sync
```

## Usage

### Command Line

```bash
cd spiers/ifind-5ifin
python spider.py
```

### Python API

```python
from ifind_5ifin.spider import get_ifind_data

# Fetch all categories
results = get_ifind_data()

# Fetch specific categories
results = get_ifind_data(categories=["news", "fund"])

# Access results
news = results["news"]
quotes = results["stock_quotes"]
industry = results["industry_data"]
funds = results["fund_ranking"]
```

## Data Categories

### 1. Financial News (financial_news)
- **Sources**: Stock news, finance news, global news
- **Fields**: title, summary, category, source, publish_date, source_url
- **Frequency**: Real-time

### 2. Stock Quotes (stock_quotes)
- **Sources**: Stock quotes, index quotes, fund quotes
- **Fields**: stock_code, stock_name, price, change_value, change_pct, volume, turnover, market_cap, pe_ratio, trade_date
- **Frequency**: Delayed (15-30 min)

### 3. Industry Data (industry_data)
- **Sources**: Industry overview, sector trends
- **Fields**: industry_name, change_pct, turnover, leading_stock, leading_change, sentiment
- **Frequency**: Daily

### 4. Fund Ranking (fund_ranking)
- **Sources**: Fund ranking, NAV data
- **Fields**: fund_code, fund_name, fund_type, nav, accumulated_nav, return_1d, return_1w, return_1m, return_3m, return_1y
- **Frequency**: Daily

## Output

### SQLite Database
Location: `data/ifind_5ifin.db`

Tables:
- `financial_news`
- `stock_quotes`
- `industry_data`
- `fund_ranking`

### JSON Export
Location: `output/ifind_data.json`

Contains all extracted data in JSON format.

## Anti-Bot Measures

This spider implements:
- Browser impersonation (Chrome)
- Stealthy headers
- Rate limiting (3s delay between requests)
- Proper User-Agent rotation

## Rate Limiting

- **Delay**: 3 seconds between requests
- **Concurrent Requests**: 1 (sequential)
- **Timeout**: 30 seconds per request

## Known Limitations

1. **Subscription Wall**: Advanced features require iFinD subscription
2. **Delayed Quotes**: Stock data is delayed 15-30 minutes
3. **JavaScript Rendering**: Some pages may require browser automation
4. **Limited Historical Data**: Public site has limited historical coverage

## Troubleshooting

### No data extracted
- Check if iFinD website is accessible
- Verify HTML structure hasn't changed
- Check logs for HTTP errors

### HTTP 403/429 errors
- Increase delay between requests
- Check if IP is blocked
- Consider using proxy rotation

### Missing fund data
- Fund ranking pages may have different layouts
- Check source HTML structure
- Update CSS selectors accordingly

## Data Quality

| Data Type | Reliability | Timeliness | Completeness |
|-----------|-------------|------------|--------------|
| News | ⭐⭐⭐⭐⭐ | Real-time | High |
| Stock Quotes | ⭐⭐⭐⭐ | Delayed | Medium |
| Industry Data | ⭐⭐⭐⭐ | Daily | Medium |
| Fund Ranking | ⭐⭐⭐⭐⭐ | Daily | High |

## Comparison with Alternatives

| Feature | iFinD (Public) | iFinD (Terminal) | Eastmoney | Wind |
|---------|----------------|------------------|-----------|------|
| Cost | Free | ¥¥ | Free | ¥¥¥ |
| News | ✅ | ✅ | ✅ | ✅ |
| Real-time Data | Delayed | ✅ | ✅ | ✅ |
| Fund Ranking | ✅ | ✅ | ✅ | ✅ |
| Stock Screening | ❌ | ✅ | ✅ | ✅ |
| API Access | ❌ | ✅ | ✅ | ✅ |

## Recommended Use Cases

✅ **Good for**:
- Financial news monitoring
- Fund ranking analysis
- Industry trend tracking
- Basic market overview

❌ **Not suitable for**:
- Real-time trading decisions
- High-frequency data analysis
- Advanced quantitative research
- Custom screening

## License

Data sourced from Tonghuashun (同花顺). For commercial use, verify licensing terms with Tonghuashun.

## See Also

- [Eastmoney Choice Spider](../eastmoney-choice/) - Free alternative with more public data
- [Wind Financial Spider](../wind-financial/) - Premium financial terminal
- [SMM Metals Spider](../smm-metals/) - Non-ferrous metal prices

---

**Last Updated**: 2026-07-31  
**Status**: ✅ Ready  
**Access**: Public (limited) / Premium (full)
