# MOA Government Spider - 农业农村部数据爬虫

Specialized spider for extracting Ministry of Agriculture and Rural Affairs (MOA) data including agricultural statistics, crop yields, livestock data, policy documents, and rural economy indicators.

## Data Sources

- **Primary Source**: https://www.moa.gov.cn/
- **Data Type**: Agricultural statistics, crop yields, livestock data, policy documents
- **Update Frequency**: Quarterly/Annual (varies by data type)
- **Coverage**: National agricultural production, policy, rural economy

## Features

- Extracts agricultural production statistics
- Tracks crop yield data by province and crop type
- Collects livestock and poultry production data
- Gathers policy documents and regulations
- Monitors rural economic indicators
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
from spiers.moa_gov_scraper.spider import MoAGovSpider

# Run the spider
result = MoAGovSpider().start()

# Check statistics
print(f"Scraped {result.stats.items_scraped} items")
print(f"Requests: {result.stats.requests_count}")
```

### Command Line

```bash
cd spiers/moa-gov-scraper
python spider.py
```

## Output

### SQLite Database (`data/moa_gov.db`)

**Table: `agricultural_statistics`**
- `period`: Statistical period
- `indicator`: Indicator name
- `value`: Indicator value
- `unit`: Unit of measurement
- `category`: Category (crop/livestock/fishery)
- `region`: Region/province
- `change_pct`: Year-over-year change (%)
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `crop_yield`**
- `year`: Year
- `province`: Province
- `crop_name`: Crop name
- `area`: Planting area (thousand hectares)
- `area_unit`: Area unit
- `total_output`: Total output (ten thousand tons)
- `output_unit`: Output unit
- `yield_per_area`: Yield per unit area
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `livestock_data`**
- `period`: Statistical period
- `province`: Province
- `animal_type`: Animal type (pig/cattle/sheep/poultry)
- `indicator`: Indicator name
- `value`: Indicator value
- `unit`: Unit of measurement
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `policy_documents`**
- `title`: Document title
- `publish_date`: Publication date
- `doc_number`: Document number
- `doc_type`: Document type (policy/notice/regulation/opinion/plan)
- `issuing_body`: Issuing organization
- `category`: Category (agriculture/livestock/fishery/rural)
- `summary`: Document summary
- `content`: Full content (if available)
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

**Table: `rural_economy`**
- `period`: Statistical period
- `province`: Province
- `indicator`: Indicator name
- `value`: Indicator value
- `unit`: Unit of measurement
- `source_url`: Source URL
- `scraped_at`: Timestamp of extraction

### JSON Export (`output/moa_gov.json`)

Clean JSON with separate arrays for each data type:
- `agricultural_statistics`: Agricultural production statistics
- `crop_yield`: Crop yield data by province
- `livestock_data`: Livestock production data
- `policy_documents`: Policy documents and regulations
- `rural_economy`: Rural economic indicators

## Data Coverage

| Data Type | Coverage | Update Frequency |
|-----------|----------|------------------|
| Agricultural Statistics | National/Provincial | Quarterly/Annual |
| Crop Yield | By Province & Crop | Annual |
| Livestock Data | By Province & Animal Type | Quarterly |
| Policy Documents | National Policy | As Published |
| Rural Economy | National/Provincial | Quarterly |

## Authentication Requirements

**None** - All data is publicly accessible on the MOA website. No login or API key required.

## Known Limitations

1. **Government Website**: MOA is an official government website with high reliability
2. **Update Frequency**: Statistical data is published quarterly/annually
3. **Historical Data**: Some historical data may require accessing statistical yearbooks
4. **Data Format**: Some data may be in PDF or Excel format requiring additional parsing

## Troubleshooting

### No data extracted
- Check if the website is accessible: `curl -I https://www.moa.gov.cn/`
- Government websites may have temporary maintenance
- Try increasing download delay if blocked

### Missing specific data
- Verify the data exists on the website
- Check if the URL paths are correct
- Some data may be in statistical yearbooks not covered by the spider

## Data Quality

- **Reliability**: Very High (official government source)
- **Timeliness**: Quarterly/Annual updates
- **Completeness**: Comprehensive coverage of agricultural sector
- **Accuracy**: Official government statistics

## License

Data sourced from Ministry of Agriculture and Rural Affairs of China (MOA). Government data is generally public domain. Verify specific usage terms if needed.

## See Also

- [China Flower Association Spider](../chinaflower/README.md) - Flower industry data
- [Agricultural Information Spider](../agri-cn/README.md) - Agricultural product prices
- [KIFC Flower Auction Spider](../flowers_kifc/README.md) - Flower auction prices
