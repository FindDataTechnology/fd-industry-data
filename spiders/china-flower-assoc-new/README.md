# 中国花卉协会

**Priority Score:** 95/100 (HIGH)

**Source URL:** [https://www.chinaflower.org.cn](https://www.chinaflower.org.cn)

**Category:** 花卉行业 行业协会 昆明交易所

---

## Overview

This spider extracts data from **中国花卉协会**, described as: _全国性的花卉行业组织，提供花卉产业信息、标准制定、行业服务_

## Installation

```bash
cd china-flower-assoc-new
uv pip install "scrapling[fetchers]"
```

## Usage

### Basic Usage

```bash
# Run with default URL from START_URLS
python spider.py

# Crawl specific URLs
python spider.py --urls https://example.com

# Test run (no data saved)
python spider.py --dry-run
```

### With uv

```bash
uv run python spider.py
```

## Expected Data Schema

| Column | Type | Description |
|--------|------|-------------|
| source_url | string | Original URL of scraped page |
| page_title | string | HTML title of the page |
| content_length | integer | Size of page content (bytes) |
| status_code | integer | HTTP status code |
| scraped_at | timestamp | Collection timestamp |

### Output Files

- `data/data.sqlite` - SQLite database with scraped records
- `output/export.jsonl` - JSON Lines format export

## Troubleshooting

### Common Issues

**HTTPS/TLS Errors**
```bash
# Update certificates
uv pip install --upgrade certifi
```

**Connection Timeouts**
```bash
# Increase retry count in spider.py
MAX_RETRIES = 5  # instead of 3
```

**User-Agent Blocking**
- The spider rotates 5 different UAs automatically
- If still blocked, modify `CUSTOM_UAS` list in spider.py

**Rate Limiting**
- Current delay: 2 seconds between requests
- Adjust `REQUEST_DELAY` in spider.py if needed

## Data Quality

- **Priority Score:** 95/100
- **Anti-bot Measures:** 5 rotating User-Agents
- **Retry Policy:** Up to 3 retries per URL
- **Rate Limiting:** 2s delay between requests

## License

MIT License (per findindustry-data project standards)
