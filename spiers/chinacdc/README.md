# ChinaCDC Spider - 中国疾病预防控制中心数据爬虫

Specialized spider for extracting disease control and public health data from China CDC.

## Data Sources

- **Primary Source**: http://www.chinacdc.cn/
- **Data Type**: Disease statistics, epidemic data, vaccination data, public health reports
- **Update Frequency**: Weekly
- **Coverage**: National disease surveillance and immunization data

## Features

- Extracts notifiable disease statistics (17 diseases, classified by 甲/乙/丙)
- Captures epidemic event monitoring data
- Collects immunization program coverage data (9 vaccines)
- Downloads public health reports and bulletins
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
from spiers.chinacdc.spider import get_chinacdc_data

results = get_chinacdc_data()

results = get_chinacdc_data(
    categories=["disease", "epidemic"],
    include_reports=True
)
```

### Command Line

```bash
cd spiers/chinacdc
python spider.py
```

## Output

### SQLite Database (`data/chinacdc.db`)

| Table | Description |
|-------|-------------|
| `disease_stats` | Notifiable disease statistics |
| `epidemic_data` | Epidemic event monitoring |
| `vaccination_data` | Immunization coverage data |
| `health_reports` | Public health reports |

### JSON Exports

- `output/chinacdc_disease.json`
- `output/chinacdc_epidemic.json`
- `output/chinacdc_vaccination.json`
- `output/chinacdc_report.json`

## Disease Coverage

| Disease | Chinese | Category |
|---------|---------|----------|
| Cholera | 霍乱 | 甲类 |
| Plague | 鼠疫 | 甲类 |
| Viral Hepatitis | 病毒性肝炎 | 乙类 |
| Tuberculosis | 肺结核 | 乙类 |
| HIV/AIDS | 艾滋病 | 乙类 |
| COVID-19 | 新型冠状病毒感染 | 乙类乙管 |
| Influenza | 流行性感冒 | 丙类 |
| Hand-foot-mouth | 手足口病 | 丙类 |

## Vaccine Coverage

| Vaccine | Chinese | Abbreviation |
|---------|---------|--------------|
| Hepatitis B | 乙肝疫苗 | HepB |
| BCG | 卡介苗 | BCG |
| Polio | 脊灰疫苗 | OPV |
| DPT | 百白破疫苗 | DPT |
| MMR | 麻腮风疫苗 | MMR |
| Japanese Encephalitis | 乙脑疫苗 | JE |

## Authentication Requirements

**No authentication required.** All data is publicly accessible on the China CDC website. Epidemic bulletins and disease surveillance data are open public health information.

## Known Limitations

1. **Data Granularity**: Provincial-level data may not always be available
2. **Update Timing**: Weekly bulletins may be delayed during holidays
3. **Historical Data**: Older data may require navigating archived pages
4. **PDF Reports**: Some reports are in PDF format requiring additional parsing

## Data Quality

- **Reliability**: Very High (national CDC)
- **Timeliness**: Weekly updates for epidemic data
- **Completeness**: Comprehensive national disease surveillance
- **Accuracy**: Official government statistics
