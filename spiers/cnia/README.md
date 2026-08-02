# CNIA Spider - 中国有色金属工业协会数据爬虫

Specialized spider for extracting industry statistics, production data, and reports from China Non-ferrous Metal Industry Association.

## Data Sources

- **Primary Source**: https://www.cnia.org.cn
- **Data Type**: Production statistics, industry news, policy documents, analysis reports
- **Update Frequency**: Monthly/Quarterly
- **Focus**: Mining production, smelting output, industry trends

## Features

- Extracts production statistics (volume, growth rates)
- Scrapes industry news and policy documents
- Collects analysis reports (annual, monthly)
- Metal-specific content identification
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
from spiers.cnia.spider import get_cnia_data

# Fetch all data
results = get_cnia_data()

# Fetch specific data types
results = get_cnia_data(
    include_stats=True,
    include_news=True,
    include_reports=False
)

# Access results
statistics = results["statistics"]
news = results["news"]
reports = results["reports"]

for stat in statistics:
    print(f"{stat['period']} | {stat['metal_cn']} | {stat['production_volume']} {stat['production_unit']}")
```

### Command Line

```bash
cd spiers/cnia
python spider.py
```

## Output

### SQLite Database (`data/cnia.db`)

**Table: `production_statistics`**
- `period`: Statistical period (year/month)
- `metal`: Metal name (English)
- `metal_cn`: Metal name (Chinese)
- `production_volume`: Production volume
- `production_unit`: Unit (吨/万吨)
- `growth_rate`: Growth rate
- `growth_unit`: Growth unit (%/吨)
- `region`: Geographic region
- `category`: Data category
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `industry_news`**
- `date`: Publication date
- `title`: Article title
- `content`: Content summary (first 500 chars)
- `category`: Category (news/policy/association/industry)
- `metal`: Related metal
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `industry_reports`**
- `date`: Publication date
- `title`: Report title
- `summary`: Report summary
- `content`: Report content (first 1000 chars)
- `report_type`: Type (annual/monthly/analysis)
- `metal`: Related metal
- `author`: Author/source
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

### JSON Exports

- `output/cnia_statistics.json`: Production statistics
- `output/cnia_news.json`: Industry news
- `output/cnia_reports.json`: Analysis reports

## Data Types

### 1. Production Statistics
- Monthly/quarterly production volumes
- Year-over-year growth rates
- Regional breakdowns
- Metal-specific output data

### 2. Industry News
- Policy documents and regulations
- Association announcements
- Industry developments
- Market commentary

### 3. Analysis Reports
- Annual industry reports
- Monthly market analysis
- Trend analysis
- Expert commentary

## Metal Coverage

| Metal | Chinese | Symbol |
|-------|---------|--------|
| Copper | 铜 | CU |
| Aluminum | 铝 | AL |
| Zinc | 锌 | ZN |
| Lead | 铅 | PB |
| Nickel | 镍 | NI |
| Tin | 锡 | SN |

## Anti-Bot Measures

- Browser impersonation (chrome)
- Proper headers
- Rate limiting (2s delay)
- Respectful crawling

## Known Limitations

1. **Site Availability**: cnia.org.cn may have intermittent availability

2. **Content Structure**: HTML structure may change, requiring selector updates

3. **Data Frequency**: Statistics are updated monthly/quarterly, not daily

4. **Login Required**: Some premium reports may require membership

## Troubleshooting

### No data extracted
- Check if site is accessible: `curl -I https://www.cnia.org.cn`
- Verify HTML structure hasn't changed
- Check logs for HTTP errors

### Missing statistics
- Statistics section may use different URL patterns
- Check if tables are dynamically loaded
- May need to adjust selectors

## Data Quality

- **Reliability**: High (official industry association)
- **Timeliness**: Monthly/quarterly updates
- **Completeness**: Comprehensive industry coverage
- **Accuracy**: Official statistics

## Comparison with Other Sources

| Source | Focus | Update Frequency | Data Type |
|--------|-------|------------------|-----------|
| SMM | Daily prices | Daily | Market prices |
| LME | Futures | Daily | Exchange prices |
| CNIA | Industry stats | Monthly | Production data |
| China Metal | News + prices | Daily | Market context |

**Recommendation**: Use CNIA for industry-level statistics and production data, complement with SMM for daily pricing.

## License

Data sourced from China Non-ferrous Metal Industry Association (CNIA). For commercial use, verify licensing terms directly.

## See Also

- [SMM Metals Spider](../smm-metals/README.md) - Daily market prices
- [LME Metals Spider](../cnia-lme/README.md) - International futures
- [China Metal Spider](../chinametal/README.md) - Market news and analysis
