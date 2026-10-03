# WeChat Official Account Platform Spider

微信公众号平台爬虫 - 通过搜狗微信搜索获取公开文章数据。

## Source

| Property | Value |
|----------|-------|
| URL | https://mp.weixin.qq.com/ |
| Search Entry | https://weixin.sogou.com/ |
| Auth Required | Partial (see below) |
| Rate Limit | 3s delay, 2 concurrent |

## Authentication Requirements

### Public Access (This Spider)
- Public article pages shared via links (`mp.weixin.qq.com/s/...`)
- Article content, title, author, publish date
- Account name (public profile)

### Requires Authentication (NOT Covered)
- **Article metrics** (views, likes, shares) - needs Official Account login
- **Follower analytics** - needs Official Account login
- **Content management** - needs Official Account login
- **API access** - needs AppID + AppSecret from mp.weixin.qq.com

To access private data, register at https://mp.weixin.qq.com/ and use the [Official API](https://developers.weixin.qq.com/doc/offiaccount/).

## Data Types

- **WeChat Articles** - 微信公众号公开文章正文及元数据

## Quick Start

```bash
cd fd-industry-data
uv run python spiers/wechat-mp/spider.py
```

## Output

- SQLite: `data/wechat_mp.db`
- JSON: `output/wechat_mp.json`

## Anti-Bot Notes

- WeChat has aggressive anti-bot measures
- Uses Sogou WeChat Search as entry point to discover public articles
- Stealth session configured as fallback for protected pages
- Conservative rate limiting (3s delay, 2 concurrent)
