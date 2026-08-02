# CZCE Spider - Zhengzhou Commodity Exchange

**Target**: [http://www.czce.com.cn](http://www.czce.com.cn)  
**Score**: 98/100 - High-priority source

## What This Extracts
- Futures settlement prices from Zhengzhou Commodity Exchange
- Trading volumes and open interest data
- Contract specifications for agricultural products (PTA, PTA, sugar, oilseed)

## Quick Start

```bash
cd /Users/chengsishi/finddata/fd-industry-data/spiders/czce
pip install "scrapling[all]>=0.4.7"
scrapling install --force
python spider.py
```

## Output Format

### SQLite Database
Location: `data/czce.db`

Tables:
- `settlement_prices` - Daily settlement prices
- `market_notices` - Market announcements

### JSON Export
Location: `output/*.json`

## Rate Limiting & Anti-Bot

- Delay between requests: 2.0 seconds
- User-Agent rotation: Chrome impersonation
- Stealthy headers enabled
- Timeout: 30 seconds

---
*See spider.py for implementation details*
