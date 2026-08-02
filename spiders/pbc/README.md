# People's Bank of China Spider

**Target**: [https://www.pbc.gov.cn/](https://www.pbc.gov.cn/)  
**Score**: 95/100 - High-priority source

## What This Extracts
- Monetary policy announcements from People's Bank of China (中国人民银行)
- Lending rates and interest rate data
- Foreign exchange reserves
- Market operations and liquidity metrics

## Quick Start

```bash
cd /Users/chengsishi/finddata/fd-industry-data/spiders/pbc
pip install "scrapling[all]>=0.4.7"
scrapling install --force
python spider.py
```

## Output Format

### SQLite Database
Location: `data/pbc.db`

Tables:
- `market_data` - Primary data storage

### JSON Export
Location: `output/*.json`

## Rate Limiting & Anti-Bot

- Delay between requests: 2.0 seconds
- User-Agent rotation: Chrome impersonation
- Stealthy headers enabled
- Timeout: 30 seconds

---
*See spider.py for implementation details*
