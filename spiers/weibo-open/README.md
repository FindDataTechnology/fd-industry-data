# Weibo Open Platform Spider

微博开放平台爬虫 - 获取热搜话题、公开帖子、用户影响力数据。

## Source

| Property | Value |
|----------|-------|
| URL | https://weibo.com/ |
| Hot Search | https://s.weibo.com/top/summary |
| Open Platform | https://open.weibo.com/ |
| Auth Required | Partial (see below) |
| Rate Limit | 2s delay, 3 concurrent |

## Authentication Requirements

### Public Access (This Spider)
- **Hot search / trending topics** - 微博热搜榜
- **Public user profiles** - 公开用户主页
- **Public posts** - 公开微博帖子
- **Topic/hashtag pages** - 话题页面

### Requires API Key (NOT Covered)
- **Full search API** - needs Open Platform developer registration
- **User timeline API** - needs OAuth2 authorization
- **Comments/reposts API** - needs OAuth2
- **Direct messages** - needs OAuth2
- **Real-time data stream** - needs special permission

To access API data:
1. Register at https://open.weibo.com/
2. Create an app, get App Key + App Secret
3. Implement OAuth2 flow
4. See API docs: https://open.weibo.com/wiki/API

## Data Types

- **Hot Search** - 微博热搜榜（排名、话题、热度值）
- **Topic Pages** - 话题详情页（描述、帖子数、内容）
- **User Profiles** - 用户信息（粉丝数、关注数、发帖数）
- **Posts** - 公开微博帖子（正文、转发/评论/点赞数）

## Quick Start

```bash
cd fd-industry-data
uv run python spiers/weibo-open/spider.py
```

## Output

- SQLite: `data/weibo_open.db`
- JSON: `output/weibo_open.json`

## Anti-Bot Notes

- Weibo has aggressive anti-bot and login walls
- Stealth session configured for protected pages
- Some content may require login to view
- Conservative rate limiting (2s delay)
