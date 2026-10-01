# CISA Spider - China Iron & Steel Association Scraper

## Overview

Scrapyling-based spider for extracting **China Iron & Steel Association (中国钢铁工业协会)** industry data. Scrapes data from [https://www.cisa.org.cn](https://www.cisa.org.cn).

**Score: 98/100** - High-priority official industry association source

---

## What This Extracts

### 🏭 Production Statistics
- **Production Output** - Monthly/quarterly/annual steel production volumes
- **Production Efficiency** - Capacity utilization rates and efficiency metrics
- **Capacity Utilization** - Steel mill capacity and utilization data

### 📊 Trade Data
- **Import Statistics** - Steel product import volumes and values
- **Export Statistics** - Steel product export volumes and values
- **Trade Balance** - Net trade position by product category

### 💰 Price Indices
- **Price Indices** - Official CISA steel price indices
- **Regional Prices** - Regional steel pricing data
- **Product Variety Prices** - Prices by steel product type (rebar, plate, coil, etc.)

### 📈 Industry Analysis
- **Analysis Reports** - In-depth industry analysis reports
- **Market Analysis** - Current market conditions and trends
- **Market Outlook** - Future market projections and forecasts

---

## Quick Start

### 1. Install Dependencies

```bash
cd /Users/chengsishi/finddata/fd-industry-data/spiders/cisa
pip install "scrapling[all]>=0.4.7"
scrapling install --force
```

### 2. Run the Spider

```bash
python spider.py
```

Or with asyncio directly:

```bash
python -c "from spider import run_spider; import asyncio; asyncio.run(run_spider())"
```

---

## Data Output

### SQLite Database
**Location:** `data/cisa_data.db`

Tables created automatically:
- `production` - Production statistics and output data
- `trade` - Import/export trade data
- `price` - Price indices and regional pricing
- `analysis` - Industry analysis reports and articles
- `general` - General steel industry data

Each table contains:
- Title/heading
- Publication date
- Content/data
- Source URL
- Timestamp of scrape

### JSON Export
**Location:** `output/*.json`

Separate files by category:
- `production.json`
- `trade.json`
- `price.json`
- `analysis.json`

---

## Rate Limiting & Anti-Bot Protection

The spider implements comprehensive anti-bot measures:

- ✅ **User-Agent rotation** - Chrome impersonation
- ✅ **Rate limiting** - 3-second delays between requests
- ✅ **Max retries** - 3 retry attempts per request
- ✅ **Request throttling** - Max 2 concurrent requests
- ✅ **Robots.txt compliance** - Follows robots.txt rules
- ✅ **Timeout protection** - 30-second timeout per request

**Why these settings?**  
CISA's website has moderate bot detection. The conservative approach ensures reliable extraction without triggering anti-scraping mechanisms.

---

## Database Schema

```sql
CREATE TABLE {category} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at TEXT,        -- ISO timestamp of scrape
    source_url TEXT,        -- Original data source URL
    category TEXT,          -- Data category
    title TEXT,             -- Article/report title
    url TEXT,               -- Article URL
    publish_date TEXT,      -- Publication date
    content TEXT,           -- Article content (truncated)
    other_data JSONB        -- Flexible additional fields
);
```

---

## Configuration

Edit `spider.py` to customize start URLs:

```python
START_URLS = [
    "https://www.cisa.org.cn/",  # Homepage
    "https://www.cisa.org.cn/production/",  # Production statistics
    "https://www.cisa.org.cn/trade/",  # Import/export data
    "https://www.cisa.org.cn/price/",  # Price indices
    "https://www.cisa.org.cn/analysis/",  # Industry analysis
]
```

Category-specific URLs:
```python
CATEGORY_URLS = {
    "production": ["..."],
    "trade": ["..."],
    "price": ["..."],
    "analysis": ["..."],
}
```

Concurrent requests and delay settings:
```python
concurrent_requests = 2    # Conservative for CISA
download_delay = 3.0       # Seconds between requests
max_retries = 3            # Retry failures
timeout = 30              # Request timeout (seconds)
```

---

## Authentication Requirements

**No authentication required.** All industry data is publicly available on CISA's website.

---

## Known Issues

1. **Complex navigation**: CISA uses complex URL patterns with Chinese characters
2. **Dynamic content**: Some pages may require JavaScript execution for full data
3. **Pagination**: Some sections use JavaScript pagination - may require Playwright for full coverage
4. **Chinese units**: Some volumes/prices use Chinese units (万，亿) - handled automatically
5. **Report access**: Some detailed reports may require member login - not scraped

---

## License

MIT License - See parent directory LICENSE file

---

## Updates

This spider should be updated periodically as CISA changes their website structure. Monitor for:
- Category URL structure changes
- Table format updates
- New data sections
- Pagination system modifications

---

# 接入档案：chinaisa.org.cn 数据门户（扩展通道，2026-10-02）

> 中钢协**数据门户** chinaisa.org.cn 与上方既有 cisa.org.cn 官网是兄弟站，勿混淆。
> 本单元现在有**两条通道**，fd-runner 唯一入口仍是 `run_cisa`（CronJob 只调它）：
>
> 1. **legacy 通道**（cisa.org.cn 官网爬虫）：`_run_cisa_legacy`，逻辑原样保留，由 `run_cisa` 包 try/except 调用。
> 2. **chinaisa 通道**（数据门户）：独立函数 `get_chinaisa_data(limit=100)`，结果与 legacy 合并返回（合计 ≤ limit）。

## 端点与鉴权

- 主机：`https://www.chinaisa.org.cn`（裸域 chinaisa.org.cn 无 A 记录，必须用 www）
- 端点：`POST https://www.chinaisa.org.cn/gxportal/xfpt/portal/getColumnList`
- 鉴权：**免鉴权**；无 Cookie 要求（冷会话直连即可）。国内源，**直连，禁走代理**。
- 请求形态（复刻站点前端 `psUtil.post`，见 `/gxportal/xfgl/config/psUtils.js`）：
  表单只有一个字段 **`params`** = `JSON.stringify(载荷)` 后再 `encodeURI` 的字符串，
  整体按 `application/x-www-form-urlencoded` 提交。
  载荷（**参数白名单，只发实测有效字段，多余参数一律不发**）：
  ```json
  {"columnId": "<栏目id>"}
  ```
  翻页时增加 `param` 字段（值是再次 encodeURI 的分页 JSON，与站点 list.js 的
  `locationUrl(pageNo, pageSize)` 一致）：
  ```json
  {"columnId": "<栏目id>", "param": "%7B%22pageNo%22:1,%22pageSize%22:25%7D"}
  ```
  即 `param = encodeURI('{"pageNo":1,"pageSize":25}')`；**pageSize=25 为站点原生分页大小**。
  必带请求头：`Referer: https://www.chinaisa.org.cn/gxportal/xfgl/portal/list.html`、
  `X-Requested-With: XMLHttpRequest`；UA 走 FetcherSession `impersonate="chrome120"`。

## 栏目（columns，栏目 id 实测有效）

| 栏目 | columnId | 内容 | report_type |
| --- | --- | --- | --- |
| 统计发布 | `2e3c87064bdfc0e43d542d87fce8bcbc8fe0463d5a3da04d7e11b4c7d692194b` | 粗钢产量旬报 / 钢材库存旬报（旬度黄金口径，worldsteel 不覆盖中国旬度）+ 月报等 | 标题含「旬报」→ `旬报`，否则 `统计发布` |
| 综合价格指数 | `63913b906a7a663f7f71961952b1ddfa845714b5982655b773a62b85dd3b064e` | 周度「国内市场八个品种价格及指数」 | `周价格指数` |

响应为 JSON：`{"articleListHtml": "...", "article_nav": "...", "columnListHtml": "..."}`，
条目在 `articleListHtml` 的 `ul.list > li` 内（标题+`[YYYY-MM-DD]` 日期+`contentpdf.html`/`content.html` 详情链接）。

## 行 schema

`report_type / title / publish_date / url / value / unit / scraped_at / source_url`。
**value、unit 恒为 None**：数值在 `contentpdf.html`（PDF 附件）或正文页里，列表接口不落数值，
依赖白名单外的 PDF 解析库才能抽取——按「可达性抽取」纪律留空，不写脏行。

## 容错规则

- HTTP 非 200 / 响应非 JSON / `articleListHtml` 缺失（如服务端拒绝参数时返回
  `{"code":301,"message":"未获得所需要的参数"}`）→ **记录日志后跳过该列/该页，不重试、不绕反爬**。
- 翻页后若标题集合与上一页完全相同（服务端翻页未生效）→ 停止翻页，防重复行；输出前按
  (report_type, title, publish_date) 去重。
- `run_cisa` 中 legacy 通道异常被吞掉（记 warning），**不会拖死 chinaisa 通道**；反之亦然。

## Legacy 通道现状（如实记录）

`_run_cisa_legacy`（原 `run_cisa` 函数体，未改动）调用 `Spider.parse_start_response()` /
`Spider.request()`——**scrapling 0.4.12 的 Spider 没有这两个方法**，执行即抛
`AttributeError`，被 `run_cisa` 的 try/except 捕获后该通道记 warning 并返回 `[]`。
即当前合并结果实际全部来自 chinaisa 通道。另有历史遗留：模块 `__main__` 块引用了不存在的
`run_spider()`，仅影响 `python spider.py` 直接执行，不影响 fd-runner 入口。

## 真实取数证据（2026-10-02 04:19–04:20 直连实测，【直连=通/代理=未使用】）

- `get_chinaisa_data(limit=100)`：**100 行**，全部唯一（含翻页，每栏目各 50）：
  `旬报 30 + 统计发布 20 + 周价格指数 50`。
- `run_cisa(limit=10)`（合并入口）：legacy 通道 0 行（AttributeError 被捕获，见上），
  合并返回 **10 行**（`统计发布 3 + 旬报 2 + 周价格指数 5`），limit 生效。
- 旬报近 2 期（标题含旬报字样 + 日期）：
  1. `2026年9月中旬重点企业钢铁产量旬报` — **2026-09-24**
  2. `2026年9月中旬重点企业钢材库存旬报` — **2026-09-24**
  （上一期：9 月上旬产量/库存旬报，2026-09-16）
- 样例行 JSON（旬报，取数时间 2026-10-02T04:19:21）：
  ```json
  {"report_type": "旬报", "title": "2026年9月中旬重点企业钢铁产量旬报", "publish_date": "2026-09-24",
   "url": "https://www.chinaisa.org.cn/gxportal/xfgl/portal/contentpdf.html?articleId=eab4f4821dc8556634f0e0325a71f2becb62eb4ebae3d383d008ba126e363a5a&columnId=2e3c87064bdfc0e43d542d87fce8bcbc8fe0463d5a3da04d7e11b4c7d692194b",
   "value": null, "unit": null, "scraped_at": "2026-10-02T04:19:21.629141",
   "source_url": "https://www.chinaisa.org.cn/gxportal/xfgl/portal/list.html?columnId=2e3c87064bdfc0e43d542d87fce8bcbc8fe0463d5a3da04d7e11b4c7d692194b"}
  ```
- 历史深度：统计发布栏目站点分页指示 1/25（约 600 条量级）；综合价格指数栏目实测取满
  2 页 50 条仍在持续出版（周度）。翻页参数实测生效（第 2 页条目与第 1 页不同）。
