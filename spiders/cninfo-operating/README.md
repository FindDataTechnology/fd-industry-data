# cninfo-operating —— 上市公司月度经营数据（月度经营披露）

覆盖农业（牧原/天邦生猪销售简报等）、房地产（华夏幸福/保利/新城月度销售与经营简报）、
金融与能源投资（龙源电力发电量、渤海租赁 Avolon、广汇能源运营数据等）三个普惠金融优先方向。
入口：`run_cninfo_operating(limit=100, category="", keyword="", start_date="", end_date="", page_size=30) -> list[dict]`。

**核心设计约束：全文 Alfred 通道。** 本单元从发现到取正文**全链路不解析 PDF**
（spec 硬约束「公告源走文本通道」）——不引入 pypdf/pdfplumber/poppler 等任何 PDF 库，
`adjunct_url` 仅作为溯源列存在，**从不下载、从不作为解析输入**。

## 数据表面（两段式）

| 段 | 端点 | 鉴权 | 说明 |
|---|---|---|---|
| 一 · 发现 | `POST http://www.cninfo.com.cn/new/hisAnnouncement/query` | 免鉴权 | 巨潮资讯网公告全文检索，返回 `announcements[]` |
| 二之一 · 桥接 | `GET https://np-anotice-stock.eastmoney.com/api/security/ann` | 免鉴权 | 东财公告列表，按股票代码换出 `art_code` |
| 二之二 · 正文 | `GET https://np-cnotice-stock.eastmoney.com/api/content/ann` | 免鉴权 | 东财公告正文，**纯文本** `data.notice_content` |

返回的关键字段（巨潮）：`announcementId`、`secCode`、`secName`、`announcementTitle`
（**含 `<em>` 高亮标签**）、`announcementTime`（毫秒时间戳）、`adjunctUrl`（相对路径，
完整 URL = `http://static.cninfo.com.cn/` + adjunctUrl）。

依赖：**仅标准库** `json / urllib / re / datetime / time`。无 PDF 库，无三方库。

## 参数白名单（只发实测有效参数，多发一律不发）

- 巨潮（**恰好 6 个字段**，代码里有 `assert frozenset(params) == CNINFO_PARAM_KEYS` 守住）：
  `pageNum`、`pageSize`、`tabName=fulltext`、`searchkey`、`seDate=<a>~<b>`、`isHLtitle=true`。
- 东财列表：`sr=-1`、`page_size`、`page_index`、`ann_type=A`、`client_source=web`、
  `stock_list=<secCode>`、`f_node=0`、`s_node=0`。
- 东财正文：`art_code`、`client_source=web`、`page_index=1`。
- 请求头只带常规浏览器 `User-Agent` + `Accept`；不做指纹伪装、不做频次对抗、不用代理。

## 五类月度经营披露（标题词白名单）

| 类别 | 判定（按标题**内容**而非搜索词） | 代表主体 |
|---|---|---|
| `产销快报` | 含「产销快报」/「产销数据」 | 广汽、上汽、比亚迪、赛力斯、金龙汽车 |
| `销售简报` | 含「销售简报」/「销售情况简报」 | 牧原、天邦、天康生物（农业）；华夏幸福（房地产） |
| `发电量` | 含「发电量」 | 龙源电力、中国核电、特变电工、嘉泽新能 |
| `主要运营数据` | 含「主要运营数据」/「运营数据」 | 渤海租赁、广汇能源、陕西煤业、中国神华 |
| `经营简报` | 含「经营简报」/「经营情况简报」/「生产经营数据」 | 大秦铁路、顺丰控股、新城控股、中国化学 |

搜索词白名单（巨潮 `searchkey`）= `产销快报 / 销售简报 / 发电量 / 主要运营数据 / 经营简报`。
检索词与最终类别**不是一一对应**（搜「销售简报」会命中「…商品猪销售情况简报」），
所以类别一律由 `classify()` 二次判定。

## 坑位（实测 2026-10-12，逐条落实）

1. **巨潮详情页没有正文**。``bulletin_detail?announcementId=`` 返回的 HTML 里没有正文文本
   （实测搜不到「产销」「万辆」等字样），只有 meta 与 PDF 下载入口 → 正文**必须**走第二段。
2. **`announcementId` ≠ `art_code`**，两者不可换算。桥接办法是先用东财列表 API 按股票代码
   检索，再按标题匹配。匹配口径 = 归一化（去 `<em>`、去标点空白、转大写）后的双向包含关系
   ——东财标题形如 `广汽集团:广汽集团2026年9月产销快报`（前缀是简称+冒号），
   裸子串匹配会大量假失配。多命中取 `notice_date` 最新那条（A/H 双份、更正稿）。
3. **港股 / REITs 在东财列表里查不到**：`stock_list=02238 / 02714 / 09927 / 00295 /
   00916 / 180201` 等返回 `total_hits=0`。这是本源的已知覆盖边界，遇即记 `INFO` 跳过，
   **绝不猜测 art_code、绝不猜数值**（实测约占原始命中量的 20%）。
4. **标题含 `<em>`**（`isHLtitle=true` 的高亮）→ 必须先剥离，否则后续匹配全崩。
5. **东财列表翻页必须知道停在哪**：列表按 `notice_date` 倒序，翻到本批公告所属报告期即可停。
   实现传入 `floor_date` = 巨潮窗口起点前推 7 天，命中即停；硬上限 `EM_LIST_MAX_PAGES=6`
   保证最坏情况有界（既礼貌也有界）。
6. **搜索词必须轮转交织（round-robin）**：若按关键词顺序取，`limit` 小时永远只有第一个词的
   类目（实测 `limit=3` 全是「产销快报」）。本实现先把 5 个词的结果全部落地，再轮转交织输出，
   实测 `limit=3` 即可拿到 3 个不同类别、`limit=5` 拿到全部 5 类。
7. **巨潮偶发 504 Gateway Time-out**（全文检索后端不稳）。按契约 log-and-skip。

## 容错规则（spec 契约，逐条实现）

- 网络层：`urllib` 单次尝试，**`attempts=1`，不重试、不连环重试、不做反爬绕过**；
  同源请求之间 ≥0.4s 礼貌间隔。
- 标题：`<em>` 一律剥离后再落 `announcement_title` 列、再做跨站匹配。
- **东财列表匹配不到 art_code → 跳过并记 `INFO` 日志，绝不猜测数值。**
- 正文为空或 `<50` 字 → 跳过（`MIN_CONTENT_CHARS=50`）。
- `content` 截断至 4000 字符（`CONTENT_TRUNCATE`），防止超长公告撑爆下游。
- 去重键 `(sec_code, announcement_id)`，跨关键词去重。
- `limit` 真实生效：循环内 `len(rows) >= limit` 早停 + 出口再截一次 `rows[:limit]`。
- 全部 5 个搜索词都失败 → 返回 `[]`（记 warning），不抛异常污染 dispatcher。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `sec_code` / `sec_name` | 巨潮 `secCode`/`secName` | 原样；港股代码也会出现在发现段，但正文段取不到会被跳过 |
| `announcement_title` | 巨潮 `announcementTitle` | **已剥离 `<em>`** 并压平空白 |
| `announcement_id` | 巨潮 `announcementId` | 行身份；去重键的一半 |
| `art_code` | 东财列表 | 由标题匹配桥接得到，非换算得到 |
| `announce_date` | 巨潮 `announcementTime` | 毫秒时间戳 → `YYYY-MM-DD` |
| `period` | 标题推导 | 优先匹配标题里的 `2026 年 9 月`；无明文日期则回退到公告日的**上一个自然月**（月度经营数据惯例：9 月数据 10 月发） |
| `category` | `classify(标题)` | 五类之一；未命中该条跳过（不猜类） |
| `content` | 东财 `notice_content` | 压平空白、截断 4000 字 |
| `adjunct_url` | 静态前缀 + 巨潮 `adjunctUrl` | **仅溯源，从不用作解析输入** |
| `source` | 常量 | `cninfo-operating` |
| `scraped_at` | 本地 | UTC ISO8601 |

## 历史深度

- 巨潮 `seDate` 窗口任意可调，实测 2026-03-01~04-30 窗口照样能拿到全 5 类（东财列表翻页
  由 `floor_date` 自动兜到窗口起点）。
- 未显式给 `start_date` 时 = 近 **45 天**滚动窗口（`WINDOW_DAYS`），约覆盖一个披露周期。
- 更早历史受东财列表翻页硬上限（6 页 × 100 条 ≈ 600 份公告/公司）约束 —— 高公告密度公司
  （如牧原 ~3349 条）约能回看半年多；要更深只能显式缩小 `seDate` 窗口分批跑。

## 真实取数证据（2026-10-12 实测，非构造）

执行：`python3 spiders/cninfo-operating/spider.py`（`limit=3`，默认 45 天滚动窗口）
→ **3 行**，一行一个类目：

```
601777 千里科技 2026-09 产销快报 | art_code=AN202610111830334116
002714 牧原股份 2026-09 销售简报（农业）
001289 龙源电力 2026-09 发电量（能源投资）
```

`limit=10` → **10 行**，五个类目各 2 行（轮转交织生效）。

滚动窗口（2026-08-28~2026-10-12）`limit=40` → **40 行**
（`产销快报 26 / 销售简报 14`），含三领域交叉命中：

- 农业：`002714 牧原股份 2026-09 销售简报` 正文含
  `2026 年 9 月份销售商品猪 642.3 万头，同比变动 15.24%`；
  `002124 天邦食品 2026-09` 正文含 `销售商品猪 50.34 万头…销售收入 46,197.17 万元`。
- 金融/能源投资：`001289 龙源电力 2026-09 发电量` 正文含
  `完成发电量 5,666,639 兆瓦时，较 2025 年同期同比增长 7.08%`。

固定历史窗口（`start_date=2026-03-01, end_date=2026-04-30`）→ 本次 golden 锚所在批次。
样例行 JSON（`limit=12` 运行的首行，未加工）：

```json
{"sec_code": "600686", "sec_name": "金龙汽车", "announcement_title": "金龙汽车2026年3月份产销数据快报", "announcement_id": "1225082102", "art_code": "AN202604071821044393", "announce_date": "2026-04-07", "period": "2026-03", "category": "产销快报", "content": "证券代码：600686 证券简称：金龙汽车 编号：2026-006 厦门金龙汽车集团股份有限公司 2026 年 3 月份产销数据快报 本公司董事会及全体董事保证本公告内容不存在任何虚假记载、误导性陈述或者重大遗漏，并对其内容的真实性、准确性和完整性承担法律责任。 厦门金龙汽车集团股份有限公司 2026 年 3 月客车产销数据快报如下： 单位：辆 项目名称 本…", "adjunct_url": "http://static.cninfo.com.cn/finalpage/2026-04-08/1225082102.PDF", "source": "cninfo-operating", "scraped_at": "2026-10-12T15:48:16.606832+00:00"}
```

## golden 断言口径

`golden/001-cninfo-operating.json` 锚**固定历史窗口** `2026-03-01 ~ 2026-04-30`
（不锚滚动当月，避免随着披露推进而漂）：`min_rows: 12`，锚 `source`、五类 `category`
全部各至少一行、以及结构位 `sec_code=600686`（金龙汽车）/ `sec_name=金龙汽车` /
`period=2026-03` / `announcement_id=1225082102` / `art_code=AN202604071821044393`
（后两者是**不可变的站点标识符**，非随时间漂移的数值）。
`whitelist_fields` 含 `content`/`scraped_at`/`adjunct_url`/`announce_date`
（正文与时间戳是易变位，不参与断言）。实测重放：`ok=True, rows=12, diffs=[]`。

## 自检

```bash
python3 -m py_compile spiders/cninfo-operating/spider.py
python3 scripts/validate_manifests.py
python3 scripts/conformance_gate.py --roots .
python3 scripts/check_manifest_commands.py
python3 spiders/cninfo-operating/spider.py     # 冒烟：limit=3 打印真实行
```

## 遗留风险

1. **港股 / REITs 覆盖缺口**：约 20% 的巨潮发现结果在东财列表里查不到 A 股载体
   （如 `02238` 广汽 H、`00916` 龙源 H、`180201` REITs），会在日志里留 `INFO` 并跳过。
   补口需要另一条港股正文通道（不在本单元范围内，未实现、未猜数）。
2. **东财列表翻页上限**：`EM_LIST_MAX_PAGES=6`。极高公告密度公司若要三年以上历史，
   需按季度分批调用 `start_date/end_date`。
3. **巨潮 504**：偶发且服务端控制，本单元只 log-and-skip；若某次全部 5 词都 504，
   run 返回空而非报错（dispatcher 侧表现为「本次无产出」）。
4. **`period` 为推论字段**：优先读标题明文，无明文时回退「公告日的上一个自然月」——
   这是披露惯例而非站点显式字段，季度累计数型公告（如「前三季度发电量」）的 `period`
   会落为该季最后一个月，下游按披露类型解释。
