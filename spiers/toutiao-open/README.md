# Toutiao Open Platform Spider

今日头条开放平台爬虫 - 获取热榜话题、新闻文章、频道内容。

## Source

| Property | Value |
|----------|-------|
| URL | https://www.toutiao.com/ |
| Open Platform | https://open.toutiao.com/ |
| Auth Required | Partial (see below) |
| Rate Limit | 2s delay, 4 concurrent |

## Authentication Requirements

### Public Access (This Spider)
- **Public news articles** - 公开新闻文章及正文
- **Trending topics / hot list** - 今日头条热榜
- **Channel pages** - 科技、财经、社会等频道内容
- **Public user profiles** - 公开用户主页

### Requires API Key (NOT Covered)
- **Content creation API** - needs Open Platform registration
- **Analytics dashboard** - needs creator account
- **Comment management** - needs OAuth
- **Monetization data** - needs creator account

To access API data:
1. Register at https://open.toutiao.com/
2. Create an application
3. See API docs: https://open.toutiao.com/docs

## Data Types

- **Hot Items** - 热榜条目（标题、热度值、排名）
- **Articles** - 新闻文章（正文、作者、发布日期、评论数）

## Quick Start

```bash
cd fd-industry-data
uv run python spiers/toutiao-open/spider.py
```

## Output

- SQLite: `data/toutiao_open.db`
- JSON: `output/toutiao_open.json`

## Anti-Bot Notes

- Toutiao uses heavy JS rendering; most content is dynamically loaded
- Stealth session configured for protected pages
- Conservative rate limiting (2s delay)
- Some content may require login for full access
