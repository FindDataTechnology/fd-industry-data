# Quick Start Guide - Metal & Mining Data Spiders

Get started with extracting non-ferrous metal and mining data in 5 minutes.

## Installation

```bash
cd fd-industry-data
uv sync
```

## Run Your First Spider

### Option 1: SMM (Recommended for Chinese Market)

```bash
cd spiers/smm-metals
python spider.py
```

**What you get**:
- Daily spot prices for Cu, Al, Zn, Pb, Ni, Sn
- Prices in CNY/吨
- SQLite database: `data/smm_metals.db`
- JSON export: `output/smm_prices.json`

### Option 2: China Metal (Prices + News)

```bash
cd spiers/chinametal
python spider.py
```

**What you get**:
- Metal prices + market news + analysis
- Comprehensive market coverage
- SQLite database: `data/chinametal.db`
- JSON exports: `output/chinametal_*.json`

### Option 3: CNIA (Industry Statistics)

```bash
cd spiers/cnia
python spider.py
```

**What you get**:
- Production volumes and growth rates
- Industry reports and policy documents
- SQLite database: `data/cnia.db`
- JSON exports: `output/cnia_*.json`

### Option 4: LME (International Futures)

```bash
cd spiers/cnia-lme
python spider.py
```

**What you get**:
- LME settlement prices in USD/tonne
- Currency conversion (USD ↔ CNY)
- SQLite database: `data/lme_metals.db`
- JSON export: `output/lme_prices.json`

**⚠️ Note**: LME has strong anti-bot protection. May not work without proxy/subscription.

## Python API Usage

```python
# In your Python script or notebook
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "spiers"))

# Import spiders
from chinametal.spider import get_chinametal_data
from cnia.spider import get_cnia_data

# Fetch data
prices = get_chinametal_data(metals=["copper", "aluminum"])
stats = get_cnia_data()

# Use the data
for item in prices["prices"]:
    print(f"{item['date']} | {item['metal_cn']} | {item['price_avg']} {item['unit']}")
```

## Check Results

### View SQLite Database

```bash
# Using sqlite3 CLI
sqlite3 spiers/smm-metals/data/smm_metals.db

# Then run SQL queries
SELECT * FROM metal_prices ORDER BY date DESC LIMIT 10;
```

### View JSON Output

```bash
# Using jq (if installed)
cat spiers/smm-metals/output/smm_prices.json | jq '.[0:5]'

# Or just cat
cat spiers/smm-metals/output/smm_prices.json
```

## Verify Installation

Run the test script:

```bash
cd spiers
python test_metal_spiders.py
```

Expected output: `✓ All tests passed! Spiders are ready to use.`

## Common Commands

```bash
# List all spiders
ls -la spiers/

# Check spider structure
tree spiers/smm-metals/

# View spider logs (when running)
python spiers/smm-metals/spider.py 2>&1 | tee smm_run.log

# Clean output files
rm -rf spiers/*/output/*.json
rm -rf spiers/*/data/*.db
```

## Troubleshooting

### "No module named 'scrapling'"

```bash
cd fd-industry-data
uv sync
```

### "No data extracted"

1. Check if site is accessible:
   ```bash
   curl -I https://www.smm.cn
   ```

2. Check logs for errors:
   ```bash
   python spiers/smm-metals/spider.py 2>&1 | grep ERROR
   ```

3. Verify HTML structure hasn't changed (inspect in browser)

### "Cloudflare blocked"

- Increase delay in spider code: `time.sleep(5)` instead of `time.sleep(2)`
- Use residential proxy (not implemented)
- Try alternative source

## Next Steps

1. **Read individual READMEs**:
   - `spiers/smm-metals/README.md`
   - `spiers/chinametal/README.md`
   - `spiers/cnia/README.md`
   - `spiers/cnia-lme/README.md`

2. **Check complete documentation**:
   - `spiers/METAL_MINING_SPIDERS_COMPLETE.md`

3. **Customize for your needs**:
   - Modify `TARGET_URLS` in spider.py
   - Adjust `METALS` dictionary
   - Change extraction logic in `extract_*` functions

4. **Schedule regular runs**:
   ```bash
   # Add to crontab
   0 9 * * * cd /path/to/fd-industry-data/spiers/smm-metals && python spider.py
   ```

## Data Sources Summary

| Spider | Best For | Update Frequency | Access |
|--------|----------|------------------|--------|
| SMM | Chinese spot prices | Daily | Free |
| China Metal | Market context + news | Daily | Free |
| CNIA | Industry statistics | Monthly | Free |
| LME | International futures | Daily | Subscription recommended |

## Support

- Check individual spider README files
- Review `METAL_MINING_SPIDERS_COMPLETE.md`
- Inspect logs for error details
- Verify site accessibility before debugging

---

**Ready to start?** Run `python spiers/smm-metals/spider.py` now!
