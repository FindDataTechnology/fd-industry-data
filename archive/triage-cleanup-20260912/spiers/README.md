# Industry Data Spiders

Specialized web spiders for extracting industry data from authoritative sources.

## 📊 Metal & Mining Data Spiders

Complete spider templates for non-ferrous metal and mining data extraction.

### Available Spiders

| Spider | Source | Data Type | Status |
|--------|--------|-----------|--------|
| [**SMM Metals**](smm-metals/) | Shanghai Metals Market | Daily spot prices | ✅ Ready |
| [**CNIA-LME**](cnia-lme/) | London Metal Exchange | Futures settlements | ✅ Ready |
| [**China Metal**](chinametal/) | China Metal Market Network | Prices + news + analysis | ✅ Ready |
| [**CNIA**](cnia/) | China Non-ferrous Metal Industry Assoc | Production stats + reports | ✅ Ready |

### Quick Start

```bash
# Install dependencies
cd fd-industry-data
uv sync

# Run a spider
cd spiers/smm-metals
python spider.py

# Test all spiders
cd spiers
python test_metal_spiders.py
```

### Documentation

- **[QUICKSTART.md](QUICKSTART.md)** - 5-minute getting started guide
- **[METAL_MINING_SPIDERS_COMPLETE.md](METAL_MINING_SPIDERS_COMPLETE.md)** - Complete implementation guide
- **[IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)** - Implementation summary

### Metal Coverage

All spiders support 6 major non-ferrous metals:
- Copper (铜, CU)
- Aluminum (铝, AL)
- Zinc (锌, ZN)
- Lead (铅, PB)
- Nickel (镍, NI)
- Tin (锡, SN)

### Data Types

- **Price Data**: Daily spot prices, futures settlements, price changes
- **Market News**: Industry news, market commentary, analysis reports
- **Statistics**: Production volumes, growth rates, regional breakdowns
- **Reports**: Annual reports, monthly analysis, policy documents

### Output Formats

All spiders provide:
- SQLite database (structured storage)
- JSON export (easy integration)
- Comprehensive logging

---

## Other Industry Spiders

### Agriculture
- [agriculture/](agriculture/) - General agriculture data
- [flowers_kifc/](flowers_kifc/) - KIFC flower auction data

### Steel Industry
- [steel-assoc/](steel-assoc/) - Steel association data
- [steel-exchange/](steel-exchange/) - Steel exchange prices
- [steel-info/](steel-info/) - Steel market information
- [steel-market/](steel-market/) - Steel market data
- [steel-statistics/](steel-statistics/) - Steel production statistics

### Chemicals
- [chemicals/](chemicals/) - Chemical industry data

### Electronics
- [electronics/](electronics/) - Electronics industry data

### Government Statistics
- [gov-stats-nbs/](gov-stats-nbs/) - National Bureau of Statistics
- [gov-stats-moa/](gov-stats-moa/) - Ministry of Agriculture
- [gov-stats-pbc/](gov-stats-pbc/) - People's Bank of China
- [gov-stats-dce/](gov-stats-dce/) - Dalian Commodity Exchange
- [gov-stats-czce/](gov-stats-czce/) - Zhengzhou Commodity Exchange

### Financial Services
- [securities/](securities/) - Securities industry data
- [fin_platforms/](fin_platforms/) - Financial platforms data
- [**wind-financial/**](wind-financial/) - Wind Financial Terminal (万得金融终端) - News, economic indicators, market data
- [**ifind-5ifin/**](ifind-5ifin/) - Tonghuashun iFinD (同花顺iFinD) - News, stock quotes, fund rankings
- [**eastmoney-choice/**](eastmoney-choice/) - Eastmoney Choice (东方财富Choice) - **Best free source** for A-share data
- [**mysteel/**](mysteel/) - MySteel (我的钢铁网) - Steel prices, production, industry news
- [**smm-metals/**](smm-metals/) - Shanghai Metals Market (上海有色金属网) - Non-ferrous metal prices

**Complete Guide**: [FINANCIAL_PLATFORMS_COMPLETE.md](FINANCIAL_PLATFORMS_COMPLETE.md)

---

## Usage

### Python API

```python
from spiers.chinametal.spider import get_chinametal_data
from spiers.cnia.spider import get_cnia_data

# Fetch metal prices and news
data = get_chinametal_data(metals=["copper", "aluminum"])

# Fetch industry statistics
stats = get_cnia_data()
```

### Command Line

```bash
# Run individual spider
cd spiers/smm-metals
python spider.py

# Run with custom parameters
python -c "from smm_metals.spider import get_smm_prices; get_smm_prices(['copper'])"
```

---

## Testing

Run the verification script:

```bash
cd spiers
python test_metal_spiders.py
```

Expected output: `✓ All tests passed!`

---

## Documentation

Each spider includes:
- `spider.py` - Main spider implementation
- `manifest.yaml` - Data schema definition
- `README.md` - Detailed documentation
- `data/` - SQLite database output
- `output/` - JSON export files

---

## Support

- Check individual spider README files
- Review `QUICKSTART.md` for getting started
- See `METAL_MINING_SPIDERS_COMPLETE.md` for complete guide
- Inspect logs for error details

---

## License

Data sourced from respective platforms. For commercial use, verify licensing terms with each data provider.

---

**Last Updated**: 2026-07-31  
**Total Spiders**: 20+  
**Metal Spiders**: 4 (complete with documentation)
