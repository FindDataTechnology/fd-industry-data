# China Metal Market Network Spider - 中国金属市场网数据爬虫

Specialized spider for extracting comprehensive metal market data from China Metal Market Network.

## Data Sources

- **Primary Source**: https://www.chinametal.com.cn
- **Data Type**: Metal prices, market news, industry analysis
- **Update Frequency**: Daily
- **Coverage**: Copper, Aluminum, Zinc, Lead, Nickel, Tin, Steel

## Features

- Extracts daily metal prices (low, high, average)
- Scrapes market news and commentary
- Collects industry analysis reports
- Identifies metal-specific content
- Bilingual support (Chinese/English)
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
from spiers.chinametal.spider import get_chinametal_data

# Fetch all data
results = get_chinametal_data()

# Fetch specific metals with news
results = get_chinametal_data(
    metals=["copper", "aluminum"],
    include_news=True
)

# Access results
prices = results["prices"]
news = results["news"]
analysis = results["analysis"]

for item in prices:
    print(f"{item['date']} | {item['metal_cn']} | {item['price_avg']} {item['unit']}")
```

### Command Line

```bash
cd spiers/chinametal
python spider.py
```

## Output

### SQLite Database (`data/chinametal.db`)

**Table: `metal_prices`**
- `date`: Trading date
- `metal`: Metal name (English)
- `metal_cn`: Metal name (Chinese)
- `symbol`: Metal symbol
- `price_low`: Daily low price
- `price_high`: Daily high price
- `price_avg`: Daily average price
- `price_change`: Absolute price change
- `price_change_pct`: Percentage change
- `unit`: Price unit (元/吨)
- `currency`: Currency code (CNY)
- `region`: Market region (China)
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `market_news`**
- `date`: Publication date
- `title`: Article title
- `content`: Content summary (first 500 chars)
- `category`: Category (news/market/analysis)
- `metal`: Related metal
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `market_analysis`**
- Same structure as market_news, focused on analysis reports

### JSON Exports

- `output/chinametal_prices.json`: Price data
- `output/chinametal_news.json`: News and analysis

## Metal Coverage

| Metal | Chinese | Symbol | Unit |
|-------|---------|--------|------|
| Copper | 铜 | CU | 元/吨 |
| Aluminum | 铝 | AL | 元/吨 |
| Zinc | 锌 | ZN | 元/吨 |
| Lead | 铅 | PB | 元/吨 |
| Nickel | 镍 | NI | 元/吨 |
| Tin | 锡 | SN | 元/吨 |
| Steel | 钢 | ST | 元/吨 |

## Data Types

### 1. Price Data
- Daily spot prices
- Price ranges (low/high/average)
- Price changes and percentages
- Regional pricing (China market)

### 2. Market News
- Daily market commentary
- Industry developments
- Supply/demand updates
- Policy changes

### 3. Analysis Reports
- Daily market analysis
- Weekly reports
- Trend analysis
- Expert commentary

## Anti-Bot Measures

- Browser impersonation (chrome)
- Proper headers
- Rate limiting (2s delay)
- Respectful crawling

## Known Limitations

1. **Site Availability**: chinametal.com.cn may have intermittent availability

2. **Content Structure**: HTML structure may change, requiring selector updates

3. **Login Required**: Some premium content may require subscription

4. **Rate Limits**: Aggressive scraping may trigger blocks

## Troubleshooting

### No data extracted
- Check if site is accessible: `curl -I https://www.chinametal.com.cn`
- Verify HTML structure hasn't changed
- Check logs for HTTP errors

### Missing news articles
- News section may use JavaScript rendering
- Check if selectors match current HTML structure
- May need to adjust category detection logic

## Data Quality

- **Reliability**: Medium-High (industry source)
- **Timeliness**: Daily updates
- **Completeness**: Comprehensive coverage
- **Accuracy**: Official market data

## Comparison with Other Sources

| Source | Focus | Currency | Access |
|--------|-------|----------|--------|
| SMM | Chinese spot prices | CNY | Free (basic) |
| LME | International futures | USD | Subscription |
| China Metal | Chinese market + news | CNY | Free (basic) |

**Recommendation**: Use SMM for pure price data, China Metal for market context and news.

## License

Data sourced from China Metal Market Network. For commercial use, verify licensing terms directly.

## See Also

- [SMM Metals Spider](../smm-metals/README.md) - Shanghai Metals Market prices
- [LME Metals Spider](../cnia-lme/README.md) - London Metal Exchange data
