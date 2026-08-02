# China Flower Association Spider

**Target**: [https://www.chinaflower.org.cn](https://www.chinaflower.org.cn)  
**Score**: 95/100 - High-priority source

## What This Extracts
- Primary market data from China Flower Association
- Industry analytics and metrics
- Price information and trends

## Quick Start

```bash
cd /Users/chengsishi/finddata/fd-industry-data/spiders/china-flower-assoc
pip install "scrapling[all]>=0.4.7"
scrapling install --force
python spider.py
```

## Output Format

### SQLite Database
Location: `data/china-flower-assoc.db`

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
