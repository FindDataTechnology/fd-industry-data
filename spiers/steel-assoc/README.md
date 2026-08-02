# Steel Industry Associations Spider

Extracts data from major steel industry associations and authoritative sources.

## Data Sources

| Source | URL | Description |
|--------|-----|-------------|
| 中国钢铁工业协会 | https://www.cisa.org.cn | China Iron and Steel Association - official production, export/import, price indices |
| World Steel Association | https://www.worldsteel.org | Global crude steel production, demand forecasts, environmental data |
| 国家统计局 | http://www.stats.gov.cn/ | Black metal smelting monthly/yearly statistics |
| 钢之家 | https://www.steelhome.cn | Market info, price monitoring, inventory data, research reports |
| 上海期货交易所 | https://www.shfe.com.cn | Rebar, hot rolled coil futures trading data |

## Usage

```bash
cd /Users/chengsishi/finddata/fd-industry-data

# Run spider (all URLs)
uv run python spiers/steel-assoc/spider.py

# Run with specific URLs only
uv run python -c "from spiers.steel_assoc.spider import run_spider; run_spider(urls=['https://www.cisa.org.cn'])"
```

## Output

- **SQLite**: `spiers/steel-assoc/data/association_data.db`
- **JSON**: `spiers/steel-assoc/output/association_data.json`

## Database Schema

```sql
CREATE TABLE association_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT,
    indicator_name TEXT,
    value REAL,
    unit TEXT,
    source_url TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

## Customization

To add custom scraping logic, edit `spider.py` and modify the `extract_data_from_page()` function:

```python
def extract_data_from_page(html: str, page_url: str) -> list[dict[str, Any]]:
    """Implement site-specific parsing here."""
    items = []
    
    # Example: Parse table data
    # from bs4 import BeautifulSoup
    # soup = BeautifulSoup(html, 'html.parser')
    # for row in soup.find_all('tr'):
    #     cells = row.find_all('td')
    #     if len(cells) >= 3:
    #         items.append({...})
    
    return items
```

## Notes

- User agents rotate automatically to avoid detection
- Rate limiting: 2 seconds between requests
- Max retries: 3 per URL
- For production use, implement proper error handling and logging
