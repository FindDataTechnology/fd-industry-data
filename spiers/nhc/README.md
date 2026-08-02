# NHC Spider - 国家卫生健康委员会数据爬虫

Specialized spider for extracting public health data from National Health Commission (NHC).

## Data Sources

- **Primary Source**: http://www.nhc.gov.cn/
- **Data Type**: Public health statistics, disease surveillance, healthcare resources, policy documents
- **Update Frequency**: Monthly
- **Coverage**: National health statistics and policy

## Features

- Extracts public health indicators (birth rate, mortality, life expectancy)
- Captures disease surveillance data (17+ notifiable diseases)
- Collects healthcare resource allocation data
- Downloads policy documents with metadata
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
from spiers.nhc.spider import get_nhc_data

results = get_nhc_data()

results = get_nhc_data(
    categories=["public_health", "disease"],
    include_policy=True
)
```

### Command Line

```bash
cd spiers/nhc
python spider.py
```

## Output

### SQLite Database (`data/nhc.db`)

| Table | Description |
|-------|-------------|
| `public_health_stats` | Public health indicators |
| `disease_surveillance` | Notifiable disease data |
| `health_resources` | Healthcare resource data |
| `policy_documents` | Policy documents and regulations |

### JSON Exports

- `output/nhc_public_health.json`
- `output/nhc_disease.json`
- `output/nhc_resources.json`
- `output/nhc_policy.json`

## Public Health Indicators

| Indicator | Chinese | Unit |
|-----------|---------|------|
| Birth Rate | 出生率 | ‰ |
| Death Rate | 死亡率 | ‰ |
| Infant Mortality | 婴儿死亡率 | ‰ |
| Maternal Mortality | 孕产妇死亡率 | 1/10万 |
| Life Expectancy | 预期寿命 | 岁 |
| Vaccination Rate | 接种率 | % |

## Disease Coverage

Covers 17+ notifiable infectious diseases including viral hepatitis, tuberculosis, syphilis, HIV/AIDS, influenza, COVID-19, hand-foot-mouth disease, and more.

## Authentication Requirements

**No authentication required.** All data is publicly accessible on the NHC website. Policy documents are open government information.

## Known Limitations

1. **Site Structure**: NHC website has complex navigation; some data may be in sub-sites
2. **Data Format**: Statistical bulletins may be in PDF format requiring additional parsing
3. **Update Lag**: Statistical data typically published with 6-12 month lag

## Data Quality

- **Reliability**: Very High (national health authority)
- **Timeliness**: Monthly/quarterly/annual depending on indicator
- **Completeness**: Comprehensive national coverage
- **Accuracy**: Official government statistics
