# xsj-ratings —— 新世纪评级 银行跟踪评级

新源接入档案（批次六·银行业，change `add-banking-industry-sources` 任务 6.5）。

tasks.md 6.5 原文（逐条对照见下文）：

> `xsj-ratings` 接入：shxsj.com HTTP-only API（cid=50，keyword=银行）+ pdf[] 附件对象；
> pdftotext 字间空格清理；golden 锚=已知银行跟踪条目；验证=gate 0 违规+首爬 rows>0+golden 一致

| 原文要点 | 本单元实测结论 |
|---|---|
| shxsj.com HTTP-only | 确认：`https://www.shxsj.com:443` 连接被拒（curl exit 7），`http://www.shxsj.com/` 200/0.08s（2026-11-08） |
| API cid=50 | 确认：`cid=50` = 评级公告根栏目，`get_cid_article` 返回 `total=29993` |
| keyword=银行 | 确认：服务端过滤，`total` 29993 → 1081 |
| pdf[] 附件对象 | 确认：`pdf` 是对象数组 `[{name, url}]`，`url` 为 `/uploadfile/…` 相对路径 |
| pdftotext 字间空格清理 | 确认：`贵 阳农村商业 银行 股份有 限公 司`；清理规则见下 |
| golden 锚银行跟踪条目 | doc_id 36877 浙江义乌农村商业银行 AA+/AA+/稳定/2026-07-29 |

## 站点与入口

- 官网：`http://www.shxsj.com/`（上海新世纪资信评估投资服务有限公司，沪ICP备09010149号-2）。
- **只有 HTTP**：443 端口拒连。`curl -I https://www.shxsj.com/` → `curl: (7) Failed to connect ... port 443`；
  `http://www.shxsj.com/` → `HTTP 200`。因此 spider 不尝试 https，`BASE` 固定为 `http://www.shxsj.com`。
- 首页是 Vue SPA（927 字节壳，`<div id="app">` + `/js/app.*.js`），
  API 前缀从 bundle 的配置对象实读：`baseURL:"http://www.shxsj.com/"` + `apiURL:"serve/api/"`。

## 列表端点与参数

```
POST http://www.shxsj.com/serve/api/article_api/get_cid_article
Content-Type: application/x-www-form-urlencoded
Referer: http://www.shxsj.com/page

cid=50            栏目标识（评级公告根栏目）
keyword=银行       服务端关键字（匹配发行人/债券全称/PDF 文件名）
rank_type=跟踪评级  服务端评级类型
page=1            页码，1 起
```

实测过滤链（2026-11-08，`page=1`）：

| 请求 | total | per_page | last_page |
|---|---|---|---|
| `cid=50` | 29993 | 15 | 2000 |
| `cid=50&keyword=银行` | 1081 | 15 | 73 |
| `cid=50&keyword=银行&rank_type=跟踪评级` | **824** | 15 | 55 |
| `cid=50&keyword=银行&per_page=50` | 1081 | **15** | 73 |

两个坑：

1. **每页固定 15 条**，`per_page` / `limit` 这类分页大小参数上游不认（实测传 50 仍回 15 条）。
   要更多数据只能翻页，所以入口用 `pages` / `page` 而不是 `per_page`。
2. `cid=50` 是**根栏目**，其下 51 首次评级 / 61 跟踪评级 / 207 主动评级 / 71 其他信息
   都会混在一起返回；要「银行跟踪评级」必须再传 `rank_type=跟踪评级`
   （或改用 `cid=61`，实测等价：824 条）。本单元按 tasks.md 原文保 `cid=50` 默认值，
   用 `rank_type` 收窄。

栏目树（`article_api/get_article_cate`，`type=tree` 实读）：50 评级公告 →
51 首次评级（52 短融/53 中票/…/60 主体评级）、61 跟踪评级（62–70）、207 主动评级、71 其他信息（72/73/74）。

## 记录结构与字段抽取

单条：

```json
{"id":36972,"cid":"67","cate_name":"金融债券","add_time":"2026-09-30",
 "title":"","pdf":[{"name":"跟踪评级报告","url":"/uploadfile/genzongen/2026/xxx.pdf"}],
 "nav":[{"id":50,"title":"评级公告"},{"id":61,"title":"跟踪评级"},{"id":67,"title":"金融债券"}],
 "project":{"year":"2025","rank_type":"跟踪评级","main_type":"AA-","rank_level":"稳定",
            "bound_type":"AA-","rank_time":"2026-09-29","issuer":"…绿色金融债券"}}
```

| 输出列 | 上游来源 | 抽取规则 |
|---|---|---|
| `doc_id` | `id` | 公告主键 |
| `cid` | `cid` | 树叶栏目（银行金融债为 `67`） |
| `category` | `cate_name` | 原样 |
| `nav_path` | `nav[].title` | 用 `/` 连接（评级公告/跟踪评级/金融债券） |
| `rank_type` | `project.rank_type` | 跟踪评级 / 首次评级 / 主动评级 / `--` |
| `bank_name` | `project.issuer` | 去前导 `YYYY年`，截到首个「银行/信用社/农商行」为止 |
| `issuer` | `project.issuer` | 债券全称 |
| `issuer_rating` | `project.main_type` | 主体等级，`--` 原样保留 |
| `bond_rating` | `project.bound_type` | 债项等级，`--` 原样保留 |
| `outlook` | `project.rank_level` | 评级展望（稳定/正面/负面） |
| `rating_date` | `project.rank_time` | `YYYY-MM-DD` |
| `published_at` | `add_time` | 公告发布日；**可与评级日不同**（36877：评级 2026-07-29，发布 2026-08-15） |
| `pdf_name` / `pdf_url` | `pdf[0].name` / `pdf[0].url` | url 拼 `BASE` 成绝对地址 |
| `pdf_subject` | PDF 首页文本 | 仅 `pdf_text=True` 时填，见下 |
| `source` / `scraped_at` | — | 常量 / 抓取时间 |

`bank_name` 例：`2023年浙江义乌农村商业银行股份有限公司“三农”专项金融债券(第一期)` →
去「2023年」→ 截到「…农村商业银行」→ `浙江义乌农村商业银行`。

## PDF 与字间空格清理

附件直链形如
`http://www.shxsj.com/uploadfile/genzongen/2026/<中文文件名>.pdf`
（另有 `/uploadfile/baogaoen/...` 老路径，带 `212680076.IB-重庆三峡银行-…` 前缀）。
实测下载：5,421,110 字节，`application/pdf`，21 页，HTTP 200，1.59s。

`pdftotext -layout` 首页原文（真实抓取，贵阳农商行跟踪报告）：

```
贵 阳农村商业 银行 股份有 限公 司
025 年第                 期 绿 色金 融债 券
项 目负 责 人           宫晨
```

清理规则（`spider.py::_clean_pdf_text`，逐行）：

1. 全角空格 `\u3000` → 半角空格；
2. **CJK ↔ CJK** 之间的空白全部删除（`贵 阳` → `贵阳`，`股份有 限公 司` → `股份有限公司`）；
3. **CJK ↔ 中文标点** 之间的空白删除（`， 本评级` → `，本评级`）；
4. 其余连续空白折叠为 1 个空格（保留 `宫晨`/西文/数字词边界）。

结果：`贵 阳农村商业 银行 股份有 限公 司` → `贵阳农村商业银行股份有限公司`。

**已知限制**：该 PDF 的部分数字/字母因内嵌字体缺 ToUnicode 映射被丢字
（`025 年第 期` 少了 2025 的 2 和 5，`ip ing` 少了 l/p）。所以**评级数值一律取 API 的
`project` 字段**，PDF 只用于 `pdf_subject`（可选核验），不从 PDF 抽等级。
`pdf_text` 默认 `False`：单份 5MB×几十条不划算，且 API 已给全结构化字段。

## 数据质量提醒

doc_id **36880**（浙江义乌农商行 2025 年科技创新债券第一期）的 `bond_rating` 实测为
`AA+sti`——上游 `bound_type` 脏值（疑似录入串行），本单元原样保留、不猜改。
golden 因此避开该行，锚在干净的 36877 上。下游使用 `bond_rating` 时建议做等级白名单校验。

## 实测取数证据

`run_xsj_ratings(limit=200, pages=3, period="2026-07")`（2026-11-08）：

```
[xsj-ratings] page 1/55 取 15 条，过滤后 14 条（total=824）
[xsj-ratings] page 2/55 取 15 条，过滤后 15 条（total=824）
[xsj-ratings] page 3/55 取 15 条，过滤后 6 条（total=824）
ROWS 35 SECS 4.78
```

样例行：

| doc_id | bank_name | 主体/债项/展望 | rating_date | published_at |
|---|---|---|---|---|
| 36877 | 浙江义乌农村商业银行 | AA+ / AA+ / 稳定 | 2026-07-29 | 2026-08-15 |
| 36887 | 金华银行 | AA+ / AA / 稳定 | 2026-07-30 | 2026-07-30 |
| 36891 | 贵州兴义农村商业银行 | A+ / A+ / 稳定 | 2026-07-30 | 2026-07-30 |

耗时 ≈ 4.8s / 3 页（请求间隔 1.2s，符合 ≥1s 网络礼节）。

## 为什么 golden 钉 `period` + `pages`

列表按发布时间倒序滚动：今天 2026-07 的记录在第 1 页尾部，几周后就会被新公告推到第 2/3 页。
若 golden 只写 `page=1`，锚会随新数据漂走——那不是缺陷信号，是写法错。

因此入口加了 `period`（`YYYY-MM`，按 `rank_time`，缺省回退 `add_time`）与 `pages`：
golden 用 `period=2026-07, pages=3` 钉住月份并在 3 页窗口内取到 36877（实测 35 行），
锚值稳定；调度口径不变（`period` 空 = 取最新 `pages` 页）。`page` 是起始页（默认 1），
用于手动把窗口推到更深的历史页。

## 失败策略

- 单页请求：3 次重试（间隔 1s/2s）；仍失败 → 留痕跳过该页，其余页照常出数。
- 所有页都失败 → `RuntimeError("xsj-ratings: 所有页请求均失败")`。
- 过滤后无任何条目 → `RuntimeError("xsj-ratings: 无匹配条目 …")`（不静默返回空）。
- `period` 不是 `YYYY-MM` → `ValueError`。
- `pdf_text=True` 时单份 PDF 下载/pdftotext 失败 → 留痕，`pdf_subject` 留空，不影响该行其余字段。
- 同一 `doc_id` 跨页重复 → 去重保留首条。
- 上游 `--`（无评级/无展望）原样保留，不转空、不写 0。
