# Wind Financial Terminal Spider - 万得金融终端爬虫

## Overview

Scrapling-based spider for extracting public financial data from Wind Financial Terminal (万得金融终端).

## Data Sources

**Target URL**: https://www.wind.com.cn

### Publicly Accessible Data
- Financial news headlines and summaries
- Economic indicator calendar
- Market overview snapshots
- Research report titles and abstracts

### Premium Content (Subscription Required)
- Real-time market data feeds
- Historical financial statements
- Detailed economic database
- Custom analytics and screening tools

## Authentication Requirements

**Wind Terminal Access**:
- Requires paid WFT (Wind Financial Terminal) subscription
- Individual license: ~¥30,000-50,000/year
- Institutional license: Custom pricing
- API access requires separate WindQuant API license

**Public Website**:
- No authentication needed for news and basic market data
- Limited data compared to terminal
- Suitable for news monitoring and trend analysis

## Installation

```bash
cd fd-industry-data
uv sync
```

## Usage

### Command Line

```bash
cd spiers/wind-financial
python spider.py
```

### Python API

```python
from wind_financial.spider import get_wind_data

# Fetch all categories
results = get_wind_data()

# Fetch specific categories
results = get_wind_data(categories=["news", "economic"])

# Access results
news = results["news"]
indicators = results["economic_indicators"]
market = results["market_overview"]
reports = results["reports"]
```

## Data Categories

### 1. Financial News (financial_news)
- **Sources**: Financial news, market news, global news
- **Fields**: title, summary, category, source, publish_date, source_url
- **Frequency**: Daily

### 2. Economic Indicators (economic_indicators)
- **Sources**: Macro data, indicator calendar
- **Fields**: indicator_name, indicator_value, indicator_unit, release_date, previous_value, yoy_change, region
- **Frequency**: Daily/Weekly/Monthly (varies by indicator)

### 3. Market Overview (market_overview)
- **Sources**: Market data, stock overview
- **Fields**: index_name, index_value, change_value, change_pct, volume, turnover, trade_date
- **Frequency**: Daily

### 4. Research Reports (research_reports)
- **Sources**: Research reports, industry analysis
- **Fields**: title, author, institution, category, abstract, publish_date
- **Frequency**: Daily

## Output

### SQLite Database
Location: `data/wind_financial.db`

Tables:
- `financial_news`
- `economic_indicators`
- `market_overview`
- `research_reports`

### JSON Export
Location: `output/wind_data.json`

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

1. **Subscription Wall**: Most detailed data requires Wind terminal subscription
2. **JavaScript Rendering**: Some pages may require browser automation (not implemented)
3. **Dynamic Content**: Real-time data not accessible without API
4. **Regional Restrictions**: Some content may be geo-restricted

## Troubleshooting

### No data extracted
- Check if Wind website is accessible
- Verify HTML structure hasn't changed
- Check logs for HTTP errors

### HTTP 403/429 errors
- Increase delay between requests
- Check if IP is blocked
- Consider using proxy rotation

### Missing fields
- Some pages have different layouts
- Check source HTML structure
- Update CSS selectors accordingly

## Data Quality

| Data Type | Reliability | Timeliness | Completeness |
|-----------|-------------|------------|--------------|
| News | ⭐⭐⭐⭐⭐ | Real-time | High |
| Economic Indicators | ⭐⭐⭐⭐ | Varies | Medium (public) |
| Market Overview | ⭐⭐⭐⭐ | Delayed | Medium |
| Reports | ⭐⭐⭐⭐⭐ | Daily | Medium |

## Comparison with Alternatives

| Feature | Wind (Public) | Wind (Terminal) | Eastmoney | iFinD |
|---------|---------------|-----------------|-----------|-------|
| Cost | Free | ¥¥¥ | Free | ¥¥ |
| News | ✅ | ✅ | ✅ | ✅ |
| Real-time Data | ❌ | ✅ | ✅ | ✅ |
| Financial Statements | ❌ | ✅ | ✅ | ✅ |
| Economic Database | Limited | Full | ✅ | ✅ |
| API Access | ❌ | ✅ | ✅ | ✅ |

## Recommended Use Cases

✅ **Good for**:
- Financial news monitoring
- Economic calendar tracking
- Market trend analysis
- Research report discovery

❌ **Not suitable for**:
- Real-time trading decisions
- Quantitative analysis
- Historical data backfill
- Automated trading systems

## License

Data sourced from Wind Information Co., Ltd. For commercial use, verify licensing terms with Wind.

## See Also

- [Eastmoney Choice Spider](../eastmoney-choice/) - Free alternative with extensive public data
- [iFinD Spider](../ifind-5ifin/) - Another financial data platform
- [SMM Metals Spider](../smm-metals/) - Non-ferrous metal prices

---

**Last Updated**: 2026-07-31  
**Status**: ✅ Ready  
**Access**: Public (limited) / Premium (full)
