# CCFA Spider - 中国连锁经营协会数据爬虫

Specialized spider for extracting retail industry statistics, chain store data, and franchise information from China Chain Store & Franchise Association.

## Data Sources

- **Primary Source**: http://www.ccfa.org.cn/
- **Data Type**: Retail statistics, chain store data, franchise information, market trends
- **Update Frequency**: Monthly
- **Focus**: Chain store revenue, franchise counts, retail market size, top chain rankings

## Features

- Extracts chain store revenue and market statistics
- Retail category identification (supermarket, convenience store, department store, etc.)
- Franchise data with brand names and investment details
- Chain store rankings with revenue and store count
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
from spiers.ccfa.spider import get_ccfa_data

results = get_ccfa_data()
results = get_ccfa_data(include_stats=True, include_franchise=True, include_rankings=True, include_news=False)
```

### Command Line

```bash
cd spiers/ccfa
python spider.py
```

## Output

### SQLite Database (`data/ccfa.db`)

**Table: `chain_statistics`**
- `period`, `category`, `category_cn`, `indicator_name`, `value`, `unit`
- `growth_rate`, `growth_unit`, `region`, `source_url`, `scraped_at`

**Table: `franchise_data`**
- `period`, `brand_name`, `industry`, `store_count`, `franchise_fee`, `total_investment`
- `source_url`, `scraped_at`

**Table: `chain_rankings`**
- `year`, `rank`, `company_name`, `revenue`, `revenue_unit`, `store_count`, `industry`
- `source_url`, `scraped_at`

**Table: `industry_news`**
- `date`, `title`, `content`, `category`, `source_url`, `scraped_at`

### JSON Exports

- `output/ccfa_chain_statistics.json`
- `output/ccfa_franchise.json`
- `output/ccfa_rankings.json`
- `output/ccfa_news.json`

## Retail Category Coverage

| Category | Chinese | Type |
|----------|---------|------|
| Supermarket | 超市 | Retail |
| Convenience Store | 便利店 | Retail |
| Department Store | 百货 | Retail |
| Specialty Store | 专业店 | Retail |
| Shopping Mall | 购物中心 | Retail |
| Restaurant | 餐饮 | Food Service |
| Hotel | 酒店 | Hospitality |
| Pharmacy | 药店 | Healthcare |
| Electronics | 家电 | Consumer Electronics |
| Apparel | 服装 | Fashion |

## Authentication Requirements

- **No authentication required** for public statistics and news
- Some detailed industry reports may require CCFA membership
- Rankings data (Top 100) is publicly available

## Anti-Bot Measures

- Browser impersonation (chrome)
- Rate limiting (2s delay between requests)
- Respectful crawling with proper User-Agent

## Known Limitations

1. Site structure may change, requiring selector updates
2. Statistics are updated monthly/quarterly
3. Some detailed reports require paid membership
4. Regional granularity varies by data type

## Data Quality

- **Reliability**: High (official industry association)
- **Timeliness**: Monthly/quarterly updates
- **Completeness**: Comprehensive chain store and franchise coverage
