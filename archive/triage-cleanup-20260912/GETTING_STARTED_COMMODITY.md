# Getting Started with Chinese Commodity Exchange Spiders

## Quick Start (5 Minutes)

### 1. Install Dependencies

```bash
cd /Users/chengsishi/finddata/fd-industry-data
pip install "scrapling[all]>=0.4.7"
scrapling install --force
```

### 2. Run a Single Spider

```bash
# Test with SHFE (Shanghai Futures Exchange)
cd spiders/shfe
python spider.py
```

### 3. Check Output

```bash
# View SQLite database
sqlite3 data/shfe_prices.db "SELECT count(*) FROM steel_futures;"

# View JSON export
cat output/steel_futures.json | head -50
```

---

## What You Get

### 📊 Data Coverage

| Exchange | Products | Data Types |
|----------|----------|------------|
| **SHFE** | Steel, Metals, Energy, Rubber | Prices, Volume, Open Interest |
| **DCE** | Agriculture, Chemicals, Livestock | Prices, Volume, Analysis |
| **ZCE** | Agriculture, Chemicals, Coal/Coke | Spot & Futures Prices |
| **CISA** | Steel Industry | Production, Trade, Price Indices |

### 💾 Storage

- **SQLite Database:** `spiders/{name}/data/{name}_prices.db`
- **JSON Export:** `spiders/{name}/output/{category}.json`

---

## Running All Spiders

### Option 1: Batch Runner

```bash
cd /Users/chengsishi/finddata/fd-industry-data
python scripts/run_commodity_spiders.py
```

### Option 2: Individual Execution

```bash
# Run each spider separately
cd spiders/shfe && python spider.py
cd ../dce && python spider.py
cd ../zce && python spider.py
cd ../cisa && python spider.py
```

---

## Testing

### Run Test Suite

```bash
cd /Users/chengsishi/finddata/fd-industry-data
python scripts/test_commodity_spiders.py
```

### Expected Output

```
============================================================
Chinese Commodity Exchange Spiders - Test Suite
============================================================
Testing spider imports...
✓ SHFE spider imported successfully
✓ DCE spider imported successfully
✓ ZCE spider imported successfully
✓ CISA spider imported successfully

Testing spider attributes...
✓ SHFE spider has all required attributes
✓ DCE spider has all required attributes
✓ ZCE spider has all required attributes
✓ CISA spider has all required attributes

Testing manifest files...
✓ SHFE manifest.yaml is valid
✓ DCE manifest.yaml is valid
✓ ZCE manifest.yaml is valid
✓ CISA manifest.yaml is valid

Testing README files...
✓ SHFE README.md exists
✓ DCE README.md exists
✓ ZCE README.md exists
✓ CISA README.md exists

============================================================
Test Summary
============================================================
Total: 16/16 tests passed
============================================================
```

---

## Data Access Examples

### Python

```python
import sqlite3
import json

# Connect to SHFE database
conn = sqlite3.connect('spiders/shfe/data/shfe_prices.db')
conn.row_factory = sqlite3.Row

# Get latest steel futures
rows = conn.execute("""
    SELECT * FROM steel_futures 
    ORDER BY scraped_at DESC 
    LIMIT 10
""").fetchall()

for row in rows:
    data = json.loads(row['data'])
    print(f"{data['commodity_name']}: ¥{data['settlement_price']}")

conn.close()
```

### SQLite CLI

```bash
# Query latest prices
sqlite3 spiders/shfe/data/shfe_prices.db \
  "SELECT scraped_at, data FROM steel_futures ORDER BY id DESC LIMIT 5;"

# Count records by category
sqlite3 spiders/dce/data/dce_prices.db \
  "SELECT category, count(*) FROM agriculture GROUP BY category;"

# Export to CSV
sqlite3 spiders/zce/data/zce_prices.db \
  "SELECT * FROM agricultural_spot;" > output.csv
```

---

## Configuration

### Rate Limiting

All spiders use conservative settings to avoid detection:

```python
concurrent_requests = 2    # Max 2 parallel requests
download_delay = 3.0       # 3 seconds between requests
max_retries = 3            # Retry failed requests
timeout = 30              # 30-second timeout
```

### Customization

Edit `spider.py` in each spider directory to:

1. **Add/Remove URLs**
   ```python
   START_URLS = [
       "https://www.shfe.com.cn/data/marketdata.shtml",
       # Add your URLs here
   ]
   ```

2. **Adjust Rate Limiting**
   ```python
   download_delay = 5.0  # Increase delay for safer scraping
   ```

3. **Modify Extraction Logic**
   ```python
   async def _extract_steel_futures(self, response: Response):
       # Custom extraction logic
       pass
   ```

---

## Troubleshooting

### Issue: No data scraped

**Solution:**
1. Check internet connection
2. Verify website is accessible
3. Check spider logs for errors
4. Try increasing `timeout` value

### Issue: Slow scraping

**Solution:**
1. Increase `concurrent_requests` (carefully)
2. Decrease `download_delay` (risk of detection)
3. Check network latency

### Issue: Blocked by anti-bot

**Solution:**
1. Increase `download_delay` to 5-10 seconds
2. Add proxy rotation
3. Use different user-agent
4. Wait before retrying

### Issue: Missing data fields

**Solution:**
1. Check website structure changes
2. Update CSS selectors in spider
3. Review extraction logic
4. Check JSON output for partial data

---

## Best Practices

### 1. Respect Rate Limits
- Don't decrease delays below 2 seconds
- Monitor for 403/429 errors
- Implement exponential backoff

### 2. Data Validation
- Check row counts after each run
- Verify JSON export completeness
- Validate data types and ranges

### 3. Error Handling
- Review spider logs regularly
- Implement retry logic for failures
- Set up monitoring/alerting

### 4. Storage Management
- Archive old data (> 1 year)
- Implement cleanup scripts
- Monitor database size

### 5. Legal Compliance
- Review robots.txt
- Check terms of service
- Respect data usage policies

---

## File Structure

```
fd-industry-data/
├── spiders/
│   ├── shfe/
│   │   ├── spider.py          # Main spider
│   │   ├── manifest.yaml      # Configuration
│   │   ├── README.md          # Documentation
│   │   ├── data/              # SQLite database
│   │   ├── output/            # JSON exports
│   │   └── .gitignore         # Git ignore
│   ├── dce/
│   │   └── ... (same structure)
│   ├── zce/
│   │   └── ...
│   └── cisa/
│       └── ...
├── scripts/
│   ├── test_commodity_spiders.py    # Test suite
│   └── run_commodity_spiders.py     # Batch runner
├── COMMODITY_SPIDERS_COMPLETE.md    # Implementation summary
├── COMMODITY_DATA_DICTIONARY.md     # Data schema reference
└── GETTING_STARTED_COMMODITY.md     # This file
```

---

## Next Steps

### 1. Explore the Data
- Query SQLite databases
- Analyze JSON exports
- Build dashboards/visualizations

### 2. Automate Scraping
- Set up cron jobs
- Implement scheduling
- Add monitoring/alerting

### 3. Extend Coverage
- Add more data sources
- Enhance extraction logic
- Improve data validation

### 4. Integrate with Other Tools
- Connect to data pipelines
- Export to PostgreSQL/MySQL
- Feed into analytics platforms

---

## Support & Resources

### Documentation
- Individual spider README files
- Manifest configuration files
- Data dictionary reference

### Testing
- Run test suite regularly
- Check for regressions
- Validate data quality

### Community
- Review Scrapyling documentation
- Check spider source code
- Examine test output

---

## Summary

✅ **4 Spiders Ready:** SHFE, DCE, ZCE, CISA  
✅ **Comprehensive Coverage:** Steel, Agriculture, Chemicals, Energy  
✅ **Production Ready:** Anti-bot protection, rate limiting  
✅ **Well Documented:** README, manifest, data dictionary  
✅ **Tested:** 16/16 tests passing  
✅ **Easy to Use:** Simple commands, clear structure

**Start scraping now:**

```bash
cd /Users/chengsishi/finddata/fd-industry-data/spiders/shfe
python spider.py
```

---

**Implementation Date:** July 31, 2026  
**Spider Framework:** Scrapyling >= 0.4.7  
**Python Version:** >= 3.10  
**License:** MIT
