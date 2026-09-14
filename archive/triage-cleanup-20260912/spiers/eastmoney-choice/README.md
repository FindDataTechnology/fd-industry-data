# Eastmoney Choice Spider - 东方财富Choice爬虫

## Overview

Scrapling-based spider for extracting public financial data from Eastmoney Choice and data.eastmoney.com (东方财富Choice金融终端).

## Data Sources

**Target URLs**: 
- https://choice.eastmoney.com (Choice terminal)
- https://data.eastmoney.com (Public data center)

### Publicly Accessible Data (data.eastmoney.com)
- A-share real-time/delayed quotes
- Historical K-line data
- Financial statement summaries
- Fund rankings and NAV
- Index data
- Sector/industry performance
- Margin trading data
- Block trades (大宗交易)
- Shareholder information
- Macro economic data (GDP, CPI, PMI, etc.)

### Premium Content (Choice Subscription Required)
- Advanced stock screening
- Custom financial models
- Analyst consensus
- Institutional research database
- Excel plugin data feed

## Authentication Requirements

**Choice Terminal Access**:
- Requires paid Choice subscription
- Individual license: ~¥5,000-15,000/year
- Institutional license: Custom pricing

**Public Data Center (data.eastmoney.com)**:
- **No authentication required**
- Extensive free public data
- Best free source for A-share data
- No API key needed for web scraping

## Installation

```bash
cd fd-industry-data
uv sync
```

## Usage

### Command Line

```bash
cd spiers/eastmoney-choice
python spider.py
```

### Python API

```python
from eastmoney_choice.spider import get_eastmoney_data

# Fetch all categories
results = get_eastmoney_data()

# Fetch specific categories
results = get_eastmoney_data(categories=["stock", "macro"])

# Access results
stocks = results["stock_data"]
financials = results["financial_statements"]
funds = results["fund_data"]
indices = results["index_data"]
macro = results["macro_data"]
```

## Data Categories

### 1. Stock Data (stock_data)
- **Sources**: Stock quotes, sector performance
- **Fields**: stock_code, stock_name, price, change_value, change_pct, volume, turnover, amplitude, high, low, open_price, prev_close, market_cap, circulating_cap, pe_ratio, pb_ratio, trade_date
- **Frequency**: Real-time/Delayed
- **Coverage**: All A-shares

### 2. Financial Statements (financial_statements)
- **Sources**: Financial reports, earnings data
- **Fields**: stock_code, stock_name, report_date, eps, bvps, roe, revenue, net_profit, profit_yoy, gross_margin, net_margin, debt_ratio
- **Frequency**: Quarterly

### 3. Fund Data (fund_data)
- **Sources**: Fund rankings, ETF data
- **Fields**: fund_code, fund_name, fund_type, manager, nav, accumulated_nav, return_1d, return_1w, return_1m, return_3m, return_6m, return_1y, return_2y, fund_scale
- **Frequency**: Daily

### 4. Index Data (index_data)
- **Sources**: Market indices, sector indices
- **Fields**: index_code, index_name, index_value, change_value, change_pct, volume, turnover, trade_date
- **Frequency**: Real-time/Delayed

### 5. Macro Data (macro_data)
- **Sources**: GDP, CPI, PMI, money supply
- **Fields**: indicator, value, unit, release_date, previous_value, yoy_change, region
- **Frequency**: Monthly/Quarterly

## Output

### SQLite Database
Location: `data/eastmoney_choice.db`

Tables:
- `stock_data`
- `financial_statements`
- `fund_data`
- `index_data`
- `macro_data`

### JSON Export
Location: `output/eastmoney_data.json`

Contains all extracted data in JSON format.

## Anti-Bot Measures

This spider implements:
- Browser impersonation (Chrome)
- Stealthy headers
- Rate limiting (2s delay between requests)
- Proper User-Agent rotation

## Rate Limiting

- **Delay**: 2 seconds between requests
- **Concurrent Requests**: 1 (sequential)
- **Timeout**: 30 seconds per request

## Known Limitations

1. **JavaScript Rendering**: Some pages use dynamic loading (may need browser automation)
2. **Pagination**: Large datasets may require pagination handling
3. **Rate Limits**: Heavy scraping may trigger rate limiting
4. **Data Delays**: Some data is delayed (not real-time)

## Troubleshooting

### No data extracted
- Check if data.eastmoney.com is accessible
- Verify HTML structure hasn't changed
- Check logs for HTTP errors

### HTTP 403/429 errors
- Increase delay between requests
- Check if IP is blocked
- Consider using proxy rotation

### Missing financial data
- Financial statement pages update quarterly
- Check report period in URL (e.g., 202412 for 2024 Q4)
- Update URL for current period

## Data Quality

| Data Type | Reliability | Timeliness | Completeness |
|-----------|-------------|------------|--------------|
| Stock Data | ⭐⭐⭐⭐⭐ | Real-time/Delayed | High |
| Financial Statements | ⭐⭐⭐⭐⭐ | Quarterly | High |
| Fund Data | ⭐⭐⭐⭐⭐ | Daily | High |
| Index Data | ⭐⭐⭐⭐⭐ | Real-time/Delayed | High |
| Macro Data | ⭐⭐⭐⭐⭐ | Monthly/Quarterly | High |

## Comparison with Alternatives

| Feature | Eastmoney (Free) | Eastmoney (Choice) | Wind | iFinD |
|---------|------------------|--------------------|----|-------|
| Cost | **Free** | ¥¥ | ¥¥¥ | ¥¥ |
| Stock Data | ✅ | ✅ | ✅ | ✅ |
| Financial Statements | ✅ | ✅ | ✅ | ✅ |
| Fund Data | ✅ | ✅ | ✅ | ✅ |
| Macro Data | ✅ | ✅ | ✅ | ✅ |
| Real-time | Delayed | ✅ | ✅ | ✅ |
| API Access | ❌ | ✅ | ✅ | ✅ |

**Recommendation**: Eastmoney is the **best free source** for A-share data. Use this spider for most financial data needs before considering paid alternatives.

## Recommended Use Cases

✅ **Good for**:
- A-share market data analysis
- Financial statement research
- Fund performance tracking
- Macro economic analysis
- Portfolio monitoring
- Academic research

❌ **Not suitable for**:
- Real-time trading systems (data is delayed)
- High-frequency trading
- Institutional-grade data feeds

## Data Coverage

### Stock Markets
- Shanghai Stock Exchange (SSE)
- Shenzhen Stock Exchange (SZSE)
- Beijing Stock Exchange (BSE)

### Fund Types
- Stock funds (股票型基金)
- Mixed funds (混合型基金)
- Bond funds (债券型基金)
- Index funds (指数基金)
- ETF funds
- QDII funds

### Macro Indicators
- GDP (国内生产总值)
- CPI (消费者物价指数)
- PMI (采购经理指数)
- Money Supply (货币供应量)
- Social Financing (社会融资规模)

## License

Data sourced from Eastmoney (东方财富). For commercial use, verify licensing terms with Eastmoney.

## See Also

- [Wind Financial Spider](../wind-financial/) - Premium financial terminal
- [iFinD Spider](../ifind-5ifin/) - Another financial data platform
- [SMM Metals Spider](../smm-metals/) - Non-ferrous metal prices
- [MySteel Spider](../mysteel/) - Steel industry data

---

**Last Updated**: 2026-07-31  
**Status**: ✅ Ready  
**Access**: **Free (extensive public data)** / Premium (advanced features)
