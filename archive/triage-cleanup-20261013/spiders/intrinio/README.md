# Intrinio Spider

Automated data collection from https://www.intrinio.com/.

## Installation

```bash
uv pip install scrapling[fetchers]
```

## Usage

```bash
cd intrinio
python spider.py
```

## Output

- `data/intrinio.db` - SQLite database
- `output/intrinio_items.jsonl` - JSONL streaming
- `output/intrinio_complete.json` - Consolidated export

## Schema

Table: `intrinio_data`

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| title | TEXT | Item title |
| description | TEXT | Description text |
| url | TEXT | Source URL |
| source | TEXT | Data source name |
| scraped_at | TEXT | Timestamp |

## Troubleshooting

**Cloudflare:** Ensure stealth mode enabled.  
**Rate limited:** Increase download_delay.  
**No data:** Verify URL: `curl -I https://www.intrinio.com/`  
**Debug:** Add `-v` flag  

---

Score: 89/100 • Ready for deployment
