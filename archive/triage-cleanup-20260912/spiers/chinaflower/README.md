# China Flower Association Spider - 中国花卉协会数据爬虫

Specialized spider for extracting China Flower Association (CFA) industry data including statistics, production data, market reports, and policy information.

## Data Sources

- **Primary Source**: https://www.chinaflower.org.cn
- **Data Type**: Industry statistics, production data, market reports, policy documents
- **Update Frequency**: Monthly/Quarterly/Weekly (varies by data type)
- **Coverage**: Flower industry statistics, regional production, market analysis

## Features

- Extracts industry statistics and annual reports
- Tracks production data by region and variety
- Collects market reports and analysis
- Gathers policy information and regulations
- Rate limiting (2s delay)
- SQLite storage + JSON export
- Robots.txt compliance

## Installation

```bash
cd fd-industry-data
uv sync
```

## Usage

### Python API

```python
from spiers.chinaflower.spider import ChinaFlowerSpider

# Run the spider
result = ChinaFlowerSpider().start()

# Check statistics
print(f"Scraped {result.stats.items_scraped} items")
print(f"Requests: {result.stats.requests_count}")
```

### Command Line

```bash
cd spiers/chinaflower
python spider.py
```

## Output

### SQLite Database (`data/chinaflower.db`)

**Table: `industry_statistics`**
- `period`: Statistical period
- `indicator`: Indicator name
- `value`: Indicator value
- `unit`: Unit of measurement
- `region`: Region/province
- `flower_type`: Flower category
- `change_pct`: Year-over-year or month-over-month change (%)
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `production_data`**
- `year`: Year
- `region`: Region/province
- `flower_name`: Flower variety
- `area`: Planting area (hectares)
- `area_unit`: Area unit
- `output`: Production output (tons)
- `output_unit`: Output unit
- `yield_per_area`: Yield per unit area
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `market_reports`**
- `title`: Report title
- `publish_date`: Publication date
- `report_type`: Report type
- `summary`: Report summary
- `content`: Full content (if available)
- `author`: Author
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `policy_info`**
- `title`: Policy title
- `publish_date`: Publication date
- `policy_type`: Policy type (policy/notice/regulation/plan)
- `issuing_body`: Issuing organization
- `doc_number`: Document number
- `summary`: Policy summary
- `content`: Full content (if available)
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

### JSON Export (`output/chinaflower.json`)

Clean JSON with separate arrays for each data type:
- `industry_statistics`: Industry statistics data
- `production_data`: Production data by region
- `market_reports`: Market analysis reports
- `policy_info`: Policy documents

## Data Coverage

| Data Type | Coverage | Update Frequency |
|-----------|----------|------------------|
| Industry Statistics | National/Regional | Monthly/Annual |
| Production Data | By Province & Variety | Quarterly/Annual |
| Market Reports | National Market | Weekly/Monthly |
| Policy Information | National Policy | As Published |

## Known Limitations

1. **Website Access**: China Flower Association website may require login for some data
2. **Update Frequency**: Statistical data is published annually/quarterly
3. **PDF Reports**: Some reports may be in PDF format requiring additional parsing
4. **Data Availability**: Not all historical data may be available online

## Troubleshooting

### No data extracted
- Check if the website is accessible: `curl -I https://www.chinaflower.org.cn`
- Some sections may require authentication
- Try increasing download delay if blocked

### Missing specific data
- Verify the data exists on the website
- Check if the URL paths are correct
- Some data may be in sub-pages not covered by the spider

## Data Quality

- **Reliability**: High (official industry association)
- **Timeliness**: Varies by data type (monthly/quarterly/annual)
- **Completeness**: Covers major flower industry metrics
- **Accuracy**: Official industry statistics

## License

Data sourced from China Flower Association (CFA). For commercial use, verify licensing terms with CFA directly.

## See Also

- [KIFC Flower Auction Spider](../flowers_kifc/README.md) - Kunming flower auction prices
- [MOA Government Spider](../moa-gov-scraper/README.md) - Ministry of Agriculture data
- [Agricultural Information Spider](../agri-cn/README.md) - Agricultural product prices
