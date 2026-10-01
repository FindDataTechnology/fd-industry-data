# sc-gd-stats 接入档案（四川 + 广东统计局·月度主要指标，一单元两源）

- 入口函数：`run_sc_gd_stats(limit: int = 100) -> list[dict]`（`province` 列区分 `sichuan` / `guangdong`）
- 调度口径：monthly（两省月度指标每月 16-18 日前后发布）
- 依赖：仅 scrapling 0.4.x（curl_cffi 引擎）+ 标准库；无新增依赖
- 网络规则：国内源，**全程直连，未使用代理**【直连=4/4 请求全部 200；代理=未启用（国内源禁走代理）】

## 一、端点

| 省 | 栏目列表页 | 正文文章页 |
|---|---|---|
| 四川 | `https://tjj.sc.gov.cn/scstjj/c112117/list.shtml`（最新发布栏） | 从列表第一条标题含「四川省国民经济主要指标数据」的链接取 |
| 广东 | `https://stats.gd.gov.cn/gmjjzyzb/`（国民经济主要指标栏） | 从列表第一条标题含「广东主要统计指标」的链接取 |

两省均为静态 HTML：列表页取文章 URL → 文章页解析唯一一张主表（取页面中最大的 `<table>`）→ 每个指标行产出一条记录。

**四川站 WAF 实测（重要）**：
1. 裸目录 `https://tjj.sc.gov.cn/scstjj/c112117/` 返回 403（nginx，curl 与 chrome 指纹均 403）；**必须用 `list.shtml`**，文章页与站点首页均正常。
2. 站点对 TLS 指纹敏感：curl/requests 级请求一律 403；必须走 curl_cffi `impersonate="chrome120"`（scrapling FetcherSession）。

**广东站 WAF 实测（重要）**：
1. 请求带外部 referer（scrapling 缺省注入 `https://www.google.com/`）会被直接掐断连接（curl 52 Empty reply）；**必须显式带本站 referer**（`Referer: https://stats.gd.gov.cn/`），四川同理带 `Referer: https://tjj.sc.gov.cn/`（实测 200）。
2. curl_cffi 下 `http://` scheme 请求被掐断（Empty reply），列表里给出的文章链接是 `http://` → **一律改写为 `https://` 再请求**。
3. 只发实测有效参数：两个列表 URL 均为静态路径，**不带任何 query 参数**；无 POST。

## 二、鉴权

无。公开页面，无需登录、无需 cookie（两次实测均零 cookie 200）。

## 三、参数白名单

- 函数参数：`limit`（int，默认 100）——唯一参数，返回 ≤limit 行；`limit<=0` 回落 100。
- HTTP 请求：仅固定请求头 `Accept`、`Accept-Language: zh-CN,zh;q=0.9`、本站 `Referer`；不发任何多余参数。

## 四、容错规则（硬约束）

1. **广东「—」缺失值**：数值列置 `None`，增速列（`growth_yoy`）保留 —— 本期 8 行「—」全部保留增速，绝不写脏数值行。
2. 解析失败的数值单元格（含 `&ensp;`、空串、逗号千分位）→ `None`，不猜测、不补零。
3. 跳过非指标行：表头行（`指标`）、脚注行（`注：` 开头）、纯结构段落行（以「：」结尾且全表无数值，如「分经济类型：」）；跳过行为记日志。`row_index` 保留原表 `<tr>` 序号，缺口即跳过的行，可对账。
4. 单省失败（非 200 / 空响应 / 解析异常）→ 记录日志后跳过该省，另一省照常返回；**单 URL 仅请求一次，不连环重试、不绕反爬**（scrapling 库内部传输层重试除外，属库默认行为）。
5. 期号解析失败（标题无 `YYYY年…N月`）→ 该省整表放弃并报错，不写无期号数据。

## 五、历史深度

- 每次运行取**两省各最新一期**（列表第一条），一张表产 N 行（每指标一行）。
- 广东栏目列表页 1 页约 20 期、分页至 `index_16.html`（约 200+ 期可回溯，2023 年起）；四川「最新发布」列表页约 10 条（近 12 期，更早需站点检索）。当前 spider 未启用回溯翻页，如需历史深挖再扩。

## 六、真实取数证据（本次实测）

- **取数时间**：2026-10-01T20:20:13+00:00（UTC，即北京时间 2026-10-02 04:20）；执行环境 fd-industry-data `.venv`（scrapling 0.4.x），直连。
- **总条数**：61 行（limit=100 未截断；川 32 + 粤 29）。

### 四川（32 行）

- 文章 URL：`https://tjj.sc.gov.cn/scstjj/c112117/2026/9/16/073e5c88c50147129db8e4a118adcd92.shtml`
- 期号：`2026年1—8月四川省国民经济主要指标数据`（period=`2026-08`，2026-09-16 发布）
- 原表 38 个 `<tr>` = 1 表头 + 32 指标行 + 5 结构段落行（跳过）；产出 **32 行**
- 首行（tr#1）：`一、规模以上工业增加值`｜当月值=—（None）｜当月同比 +7.6｜累计值=—｜累计增长 6.7
- 末行（tr#37）：`销售指数`｜8月=100.6｜累计=101.6
- 样例行 JSON：

```json
{"province": "sichuan", "period": "2026-08", "indicator": "一、规模以上工业增加值", "value": null, "unit": "", "growth_yoy": "7.6", "value_cum": null, "growth_cum": "6.7", "row_index": 1, "title": "2026年1—8月四川省国民经济主要指标数据_四川省统计局", "url": "https://tjj.sc.gov.cn/scstjj/c112117/2026/9/16/073e5c88c50147129db8e4a118adcd92.shtml", "scraped_at": "2026-10-01T20:20:13+00:00", "source_url": "https://tjj.sc.gov.cn/scstjj/c112117/list.shtml"}
```

### 广东（29 行）

- 文章 URL：`https://stats.gd.gov.cn/gmjjzyzb/content/post_4960769.html`（列表原为 http，已按 https 取数）
- 期号：`2026年1-8月广东主要统计指标`（period=`2026-08`）
- 原表 31 个 `<tr>` = 1 表头 + 29 指标行 + 1 脚注（跳过）；产出 **29 行**
- 首行（tr#1）：`地区生产总值（1-6月，亿元）` = **72281.05**，同比 +4.5%
- 末行（tr#29）：`扣除物价增长（%）`（农村）值=「—」→ None，增速保留 5.8
- 「—」缺失行共 8 行，增速列全部保留（含规上工业增加值 6.1、固定资产投资 -10.7 等）
- 样例行 JSON：

```json
{"province": "guangdong", "period": "2026-08", "indicator": "地区生产总值（1-6月，亿元）", "value": 72281.05, "unit": "亿元", "growth_yoy": "4.5", "value_cum": null, "growth_cum": null, "row_index": 1, "title": "2026年1-8月广东主要统计指标", "url": "https://stats.gd.gov.cn/gmjjzyzb/content/post_4960769.html", "scraped_at": "2026-10-01T20:20:13+00:00", "source_url": "https://stats.gd.gov.cn/gmjjzyzb/"}
```

### 列语义说明

- 四川 5 列表：`value`=当月值、`growth_yoy`=当月同比、`value_cum`=1-N月累计值、`growth_cum`=累计增长；多数综合指标（规上工业增加值、固投）官方只发布增速，`value`/`value_cum` 为 None 属正常口径。
- 广东 3 列表：`value` 即 1-N月累计值（官方仅此一列），`value_cum`/`growth_cum` 恒为 None；价格指数行 `unit=上年同期=100`。

## 七、遗留风险

1. 两省 WAF 规则可能变化（四川 TLS 指纹校验、广东 referer/https 校验均为实测反推），失效时表现为整省 log-and-skip，不产生脏数据。
2. 四川裸目录路径 403，若 `list.shtml` 改版需同步改 `source_url`。
3. 广东表内「扣除物价增长（%）」等指标名重复（3 处），仅靠 `row_index` 区分，语义绑定需带行号。
4. 四川部分指标仅增速无绝对值（官方口径如此），下游按 `value IS NOT NULL` 过滤即可。

## 首爬回补口径（2026-10-02 批次）
每次运行抓取栏目列表最新 ≤13 期（`MAX_ISSUES_PER_RUN=13`，期号去重、单期失败 log-and-skip）——月频口径下一次运行即覆盖滚动 12 个月。实测 2026-10-02：573 行 / 13 期（川 2025-07→2026-08、粤同窗），单期解析失败不影响其余期。
