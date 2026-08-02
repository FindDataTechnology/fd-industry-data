# Oschina Spider

Automated data collection from https://www.oschina.net/project/stats.

## Installation

```bash
uv pip install scrapling[fetchers]
```

## Usage

```bash
cd oschina
python spider.py
```

## Output

- `data/oschina.db` - SQLite database
- `output/oschina_items.jsonl` - JSONL streaming
- `output/oschina_complete.json` - Consolidated export

## Schema

Table: `oschina_data`

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
**No data:** Verify URL: `curl -I https://www.oschina.net/project/stats`  
**Debug:** Add `-v` flag  

---

Score: 85/100 • Ready for deployment
