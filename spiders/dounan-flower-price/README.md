# dounan-flower-price（昆明斗南花卉市场日价）

昆明市农业农村局 →「农业农村工作 / 价格行情」栏目每日发布的《云南昆明斗南花卉市场 X 月 X 日
花卉价格信息》。发布主体标注为**昆明斗南国际花卉产业园开发有限公司**。
入口函数：`run_dounan_flower_price(limit: int = 500, max_pages: int = 10, window_start: str | None = None, window_end: str | None = None) -> list[dict]`。

上游每日一版、每日约 **23 个品种**（玫瑰/百合/康乃馨/绣球/洋桔梗/菊花/金鱼草/尤加利叶/…），
每品种给出`计量单位 / 最高价 / 最低价 / 均价`四列。

## 端点与取数路径（实测 2026-11-08，UTC+8）

- 列表页：`https://nyncj.km.gov.cn/nygz/jgxq/index.shtml`（charset **UTF-8**，实测 200）
  - 每条 `li` 内：`<a href="/c/YYYY-MM-DD/<id>.shtml">标题</a> <span>YYYY-MM-DD</span>`
    （`span` 是**发布日期**）。
  - **分页命名不规则**：第 1 页是 `index.shtml`，**第 2 页起是 `index_2.shtml`**；
    `index_1.shtml` 实测 **404**。实测可见 `index.shtml` + `index_2`…`index_10`
    （`index_11` 起 404）⇒ `max_pages=10` 恰好覆盖全窗。
  - 文章链接一律**从列表页按标题正则发现**（`斗南花卉市场\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*花卉价格信息`），
    不硬编码任何单篇 URL。
- 正文页：`https://nyncj.km.gov.cn/c/<YYYY-MM-DD>/<id>.shtml`
  - 恰 **1 张表 25 行**：第 1 行为跨列标题（1 个 `td`），第 2 行表头
    `品名 | 计量单位 | 最高价 | 最低价 | 均价`，其后 23 行为品种报价。
    单元格内是 `<p>` 包文本，`_cells()` 已剥标签并做 `html.unescape`。
  - 实测样例（2026-11-05 报告，`/c/2026-11-06/5124266.shtml`）：
    `金鱼草|元/枝|1.15|0.94|1.05`、`红玫瑰（单头）|元/枝|0.35|0.22|0.27`、
    `尤加利叶|元/枝|6.1|5.4|5.75`、`茉莉花|元/把|12.8|5.23|7.27`、`绣球|元/枝|10.32|7.17|8.31`。

## 三条口径（关键坑）

1. **价格日期取自标题，不是发布日期**。该源 T+1 发布：11-05 的价格于 11-06 见报。
   `price_date` 来自标题，`publish_date` 来自列表页 `span`，**两者分别保留、绝不互相替代**。
   下游做任何日期对齐都用 `price_date`。
2. **历史只有滚动窗**。实测 2026-11-08 时最老可见 2026-09-09 —— 约 2 个月、
   42 篇报告、10 个列表页。因此本单元按「即接即累积」纪律交付：**能回补多少回补多少，
   不宣称长期历史**。更早数据需人工归档，不在本单元能力内。
3. **缺日不补零、缺价不填零、均价不派生**。上游偶有跳日（42 篇覆盖 09-09~11-05 约 58 天），
   缺日如实缺测（由 `series_state` 兜底，本单元不外插）；某品种当日无报价时该行记 None
   （`-`/`—`/`暂无`/`/`/`无` 均识别为 None）；**均价一律用上游给的值，绝不由最高/最低价推算**。

## 表头防御

表头固定五列、顺序实测从未变过。若某篇表头与 `("品名","计量单位","最高价","最低价","均价")`
不完全一致，**整篇跳过并记 warning**（不猜列序、不部分解析）——宁可缺一天，不出错列。

## 去重与截断

- 行级去重键 `(price_date, item)`：同一价格日期出现在两篇报告时只留一条。
- 结果按 `(price_date, item)` **倒序**（最新日期优先）。
- `limit` 真实生效（返回行数 ≤ limit）；**窗口过滤先于 limit 截断**，且当整篇报告的最晚
  价格日期已早于 `window_start` 时立即停止翻页（列表按发布倒序，后续只会更老）。

## 容错规则（spec 契约）

- `FetcherSession(impersonate="chrome120", timeout=25, verify=False, retries=1)`：
  **retries=1 = 单次尝试，不重试**；网络异常 / 状态非 200 → 记 warning 后跳过该 URL，
  不连环重试、不做反爬绕过。
- 列表页取不到 → 停止翻页（已取回的行照常返回）；正文页取不到 → 跳过该篇，计入 `reports_failed`。
- 标题解析不出价格日期 / 正文无表 / 无有效报价行 → 记 warning 跳过该篇，不阻塞其他篇。
- 入口读 `fd_industry_data.cancel_event.is_set()`，可被 runner 中途取消。

## 字段清单（11 列）

| 列 | 说明 |
|---|---|
| `price_date` | 价格日期（ISO，取自标题）；下游日期对齐一律用它 |
| `publish_date` | 发布日期（列表页 `span`，`YYYY-MM-DD`） |
| `item` | 品种品名（如 `红玫瑰（单头）`、`金鱼草`、`绣球`），照上游原样（含上游自带的异常写法如 `复色,玫瑰（多头）`，不做清洗） |
| `unit` | 计量单位，照上游（`元/枝` / `元/把` / `元/扎` / `元/盆` …），不归一 |
| `price_high` | 最高价（float）；缺失为 None，不填 0 |
| `price_low` | 最低价（float）；缺失为 None，不填 0 |
| `price_avg` | 均价（float，上游给值，不派生）；缺失为 None |
| `report_title` | 文章标题（`云南昆明斗南花卉市场YYYY年M月D日花卉价格信息`） |
| `report_url` | 正文页 URL |
| `source` | 固定 `dounan-flower-price` |
| `scraped_at` | UTC ISO 时间 |

概念绑定（manifest `concepts`）：`price_high/low/avg` → `price.flower.dounan.{high,low,avg}`，
`entity_type=commodity`、`measure=price`、`unit=currency_cny`、`frequency=daily`。

## 真实取数证据

- 取数时间：**2026-11-08 00:40~00:42（UTC+8）**，`.venv/bin/python`（scrapling 0.4.12）经
  `fd_industry_data.loader.load_spider_module('dounan-flower-price','spiders')` 装载实跑。
- **全量回补**：`run_dounan_flower_price(limit=1000, max_pages=10)` → **932 行**，
  日期跨度 `2026-09-09 → 2026-11-05`、**有数天数 42**、**品名 23 个**、
  三价全 None 行 **0**、均价 None 行 **0**（23×42=966，差 34 行为上游跳日）。
- **默认参数**：`limit=500` → 20 天（`2026-10-15 → 2026-11-05`），即默认值会截掉最老的日子，
  全量回补请显式调大 `limit`（首爬窗口回补按 `window_start/window_end` 派发，不受此影响）。
- **最新一版（2026-11-05）三类代表行**（未加工）：

```json
{"price_date": "2026-11-05", "publish_date": "2026-11-06", "item": "金鱼草", "unit": "元/枝", "price_high": 1.15, "price_low": 0.94, "price_avg": 1.05, "source": "dounan-flower-price"}
{"price_date": "2026-11-05", "publish_date": "2026-11-06", "item": "红玫瑰（单头）", "unit": "元/枝", "price_high": 0.35, "price_low": 0.22, "price_avg": 0.27, "source": "dounan-flower-price"}
{"price_date": "2026-11-05", "publish_date": "2026-11-06", "item": "茉莉花", "unit": "元/把", "price_high": 12.8, "price_low": 5.23, "price_avg": 7.27, "source": "dounan-flower-price"}
```

- golden 重放：`golden/001-dounan-flower-price.json` 用 `window_start=window_end=2026-11-05`
  钉死单日（同时覆盖窗口参数路径），`min_rows=20` 锁 23 品种全表。

## TLS / 网络口径

`nyncj.km.gov.cn` 为国内政府站，证书由国内 CA 签发；按仓内既有惯例统一 **verify=False**
（不假设 Linux 容器信任库含国内根）。国内源：**直连**，不走代理。实测无 WAF、无鉴权、
无 cookie、无验证码（对照：KIFA `kifa.net` 全站 468 + `sl-session` + `/.safeline/` ⇒ 雷池
SafeLine JS 挑战，不可直取，见 `spiders/flower-auction/` 的留痕）。

## 遗留风险

1. **滚动窗即上游分页上限**：栏目只保留 10 页（≈2 个月）。若上游缩短，历史深度随之缩短；
   已产出的行不受影响。真正需要长历史时应另行归档，不改本单元。
2. **golden 钉死的日期会随窗口老化**：`2026-11-05` 约在 2027-01 滚出可见窗，届时重放会
   报 `min_rows` 不足 —— 处置方式是就地更新钉死日期（与 `moa-hog-price` 同一权衡：
   golden 锁表头与数值口径，代价是钉死的日期有保质期）。
3. **分页命名是站点怪癖**（`index.shtml` + `index_2…`）：若站点改成规整 `index_1`，
   `_page_url()` 的 `page_no<=1` 分支需同步改。
4. **品种写法随上游漂移**：实测出现过 `复色,玫瑰（多头）`（半角逗号）与 `复色玫瑰（单头）`
   两种写法。本单元照抄不清洗，下游按 `item` 做序列时必须用词表映射而非字符串猜测。
