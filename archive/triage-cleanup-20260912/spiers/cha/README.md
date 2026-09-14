# CHA Spider - 中国医院协会数据爬虫

Specialized spider for extracting hospital and healthcare data from China Hospital Association (CHA).

## Data Sources

- **Primary Source**: http://www.cha.org.cn/
- **Data Type**: Hospital statistics, healthcare services, medical resources, industry reports
- **Update Frequency**: Yearly
- **Coverage**: National hospital and healthcare system data

## Features

- Extracts hospital statistics by type and level
- Captures healthcare service volume data (outpatient, inpatient, surgery)
- Collects medical resource allocation data (beds, staff, equipment)
- Covers hospital levels (3A, 3B, 2A, etc.) and types (general, specialty, TCM)
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
from spiers.cha.spider import get_cha_data

results = get_cha_data()

results = get_cha_data(
    categories=["hospital", "service"],
    include_reports=True
)
```

### Command Line

```bash
cd spiers/cha
python spider.py
```

## Output

### SQLite Database (`data/cha.db`)

| Table | Description |
|-------|-------------|
| `hospital_stats` | Hospital statistics by type/level |
| `healthcare_services` | Service volume data |
| `medical_resources` | Resource allocation data |
| `reports` | Industry reports |

### JSON Exports

- `output/cha_hospital.json`
- `output/cha_service.json`
- `output/cha_resource.json`
- `output/cha_report.json`

## Data Coverage

| Data Type | Fields |
|-----------|--------|
| Hospital Stats | Count, beds, staff, revenue by type/level/region |
| Service Data | Outpatient visits, inpatient admissions, surgery count, avg stay, bed utilization |
| Resources | Beds, physicians, nurses, health workers, equipment per 1000 population |
| Reports | Annual and special reports |

## Authentication Requirements

**No authentication required.** All data is publicly accessible on the CHA website.

## Known Limitations

1. **Site Availability**: cha.org.cn may have intermittent availability
2. **Data Granularity**: Some detailed data may require membership
3. **Update Frequency**: Annual data may lag behind current year

## Data Quality

- **Reliability**: High (official hospital association)
- **Timeliness**: Annual updates
- **Completeness**: Comprehensive national hospital coverage
- **Accuracy**: Official statistics
