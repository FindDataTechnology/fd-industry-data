# CCA Spider - 中国消费者协会数据爬虫

Specialized spider for extracting consumer complaint data, product quality reports, and satisfaction surveys from China Consumer Association.

## Data Sources

- **Primary Source**: http://www.cca.org.cn/
- **Data Type**: Consumer complaint data, market monitoring, product quality reports
- **Update Frequency**: Quarterly
- **Focus**: Complaint statistics, product recalls, consumer satisfaction, market supervision

## Features

- Extracts complaint statistics by category and period
- Product quality monitoring data (substandard products, recalls)
- Consumer satisfaction survey results
- Complaint category identification (goods, services, food, auto, etc.)
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
from spiers.cca.spider import get_cca_data

results = get_cca_data()
results = get_cca_data(include_complaints=True, include_quality=True, include_satisfaction=False, include_news=False)
```

### Command Line

```bash
cd spiers/cca
python spider.py
```

## Output

### SQLite Database (`data/cca.db`)

**Table: `complaint_statistics`**
- `period`, `category`, `category_cn`, `complaint_count`, `resolved_count`
- `resolution_rate`, `amount_involved`, `amount_unit`, `source_url`, `scraped_at`

**Table: `product_quality`**
- `date`, `product_name`, `brand`, `manufacturer`, `issue_type`, `severity`
- `recall_status`, `source_url`, `scraped_at`

**Table: `satisfaction_surveys`**
- `period`, `industry`, `industry_cn`, `score`, `sample_size`, `ranking`
- `source_url`, `scraped_at`

**Table: `industry_news`**
- `date`, `title`, `content`, `category`, `source_url`, `scraped_at`

### JSON Exports

- `output/cca_complaints.json`
- `output/cca_product_quality.json`
- `output/cca_satisfaction.json`
- `output/cca_news.json`

## Complaint Category Coverage

| Category | Chinese | Type |
|----------|---------|------|
| Goods | 商品 | Product |
| Service | 服务 | Service |
| Food | 食品 | Food Safety |
| Pharmaceutical | 药品 | Healthcare |
| Home Appliance | 家电 | Electronics |
| Automobile | 汽车 | Automotive |
| Real Estate | 房产 | Property |
| Finance | 金融 | Financial |
| Education | 教育 | Education |
| Internet | 网络 | Online Services |
| Telecom | 通信 | Telecommunications |
| Tourism | 旅游 | Travel |
| Insurance | 保险 | Insurance |
| Express Delivery | 快递 | Logistics |

## Authentication Requirements

- **No authentication required** for public complaint data and quality reports
- All consumer protection data is publicly available
- Satisfaction surveys are published openly

## Anti-Bot Measures

- Browser impersonation (chrome)
- Rate limiting (2s delay between requests)
- Respectful crawling with proper User-Agent

## Known Limitations

1. Site structure may change, requiring selector updates
2. Complaint data is updated quarterly
3. Product recall details may be limited
4. Satisfaction survey methodology varies by industry

## Data Quality

- **Reliability**: High (official consumer protection organization)
- **Timeliness**: Quarterly updates
- **Completeness**: Comprehensive consumer complaint and quality coverage
