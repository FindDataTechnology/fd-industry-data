# People's Daily Data Center Spider

人民日报数据中心爬虫 - 提取新闻文章、经济统计数据、政策文件。

## Source

| Property | Value |
|----------|-------|
| URL | https://data.people.com.cn/ |
| Paper Edition | http://paper.people.com.cn/ |
| Auth Required | No |
| Rate Limit | 2s delay, 4 concurrent |

## Data Types

- **News Articles** - 人民日报新闻文章及正文
- **Economic Data** - 经济统计数据与报告
- **Policy Documents** - 政策文件与解读
- **Paper Archive** - 人民日报电子版历史文章

## Quick Start

```bash
cd fd-industry-data
uv run python spiers/people-daily/spider.py
```

## Output

- SQLite: `data/people_daily.db`
- JSON: `output/people_daily.json`

## Authentication

No authentication required. All data is publicly accessible.

## Notes

- Some pages may require JavaScript rendering; stealth session is configured as fallback
- robots.txt is respected
- Rate limiting is conservative (2s delay) to avoid overloading the server
