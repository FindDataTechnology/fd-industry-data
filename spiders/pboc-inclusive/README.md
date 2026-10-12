# pboc-inclusive —— 央行普惠金融口径统计（小贷 / 住户信贷 / 地区社融）

新源接入档案（`templates/new-source/`）。
工单：`reports/health-tickets/`（`kind=generate`，change `inclusive-finance-sources`）。

与既有 `pboc-monetary` 的关系（设计决策 **D2**）：`pboc-monetary` 读 `116219/116319`
年度栏目下的 **xlsx 附件**（社融流量/存量 + M0/M1/M2）；本单元读 `116219/116225`
「数据解读」**扁平目录里的 HTML 报告正文**（月度/季度）。形态不同 → **新建独立单元，
不合并、不修改 `pboc-monetary` 任何文件**。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 目录页 | `GET https://www.pbc.gov.cn/diaochatongjisi/116219/116225/index.html` | 数据解读扁平目录，实测 `totalpage=38`、每页 20 条 |
| 翻页 | `.../116225/11871-<n>.html`（n=2..38） | 分页规律由页内 `totalpage` 读出，不硬编码页数 |
| 单篇 | `.../116225/<docid>/index.html` | `<docid>` 既可能纯数字（`5885724`）也可能 32 位时间戳（`2026091417020862747`） |
| 地区社融附表 | 正文内 `href="....xlsx"` 直链 | 二段取数：先抽正文直链，再取附件解析 |

三件（按标题正则分类，**目录天然倒序，故同类第一条即最新一期**）：

- **A 小额贷款公司统计**（季度，标题含 `小额贷款公司`）→ 机构数（家）、贷款余额（亿元）、余额变化（亿元）
- **B 金融统计月报**（月度，标题含 `金融统计数据报告`）→ 住户贷款/存款、人民币 & 本外币贷款余额、社融存量、M0/M1/M2
- **C 地区社融**（季度，标题含 `地区社会融资规模`）→ 31 省区增量（亿元，附表 xlsx）

入口：`run_pboc_inclusive(limit=100) -> list[dict]`（`spider.py`）。
依赖 = 标准库 `asyncio/json/re/datetime/html/zipfile/xml.etree/urllib` + **scrapling**
（`FetcherSession(impersonate="chrome")`，curl_cffi 引擎；与 carbon-monitor /
chinawuliu-logistics / sc-gd-stats / nbs_gdp 同法）。无新增第三方库；
scrapling 缺失时降级为带 Chrome UA 的 `urllib`（降级路径，非首选）。

## 实测取数证据（2026-10-12 实弹，非构造）

**目录发现**：`116225/index.html` → `totalpage=38`，首页 20 条，实测标题样例：

```
2026年8月金融统计数据报告              2026年7月金融统计数据报告
2026年上半年地区社会融资规模增量统计表   2026年一季度地区社会融资规模增量统计表
2025年三季度小额贷款公司统计数据报告     2025年二季度小额贷款公司统计数据报告
```

**A 小贷季报**（`.../116225/5885724/index.html`）正文原文：

```
截至2025年9月末，全国共有小额贷款公司4863家。贷款余额7229亿元，前三季度减少319亿元。
```

→ `小额贷款公司机构数 4863 家` / `小额贷款公司贷款余额 7229 亿元` /
`小额贷款公司贷款余额变化 319 亿元`（**已实取，非占位**）。

**B 金融统计月报**（`.../116225/2026091417020862747/index.html`，2026-08）→
`人民币贷款余额 282.35 万亿元` / `本外币贷款余额 286.15 万亿元` /
`人民币存款余额 347.67 万亿元` / `住户存款 6.99 万亿元` / `人民币贷款增量 10.23 万亿元` /
`社会融资规模存量 464.8 万亿元` / `M2 356.81` / `M1 115.77` / `M0 14.83` 万亿元。
其中 `464.8` 与 `pboc-monetary` 存量表 2026-08 交叉吻合。

**C 地区社融**（2026 上半年）→ 附表 xlsx 解析出 `北京 10581` / `天津 2684` / `河北 8521` /
`山西 2697` / `内蒙古 1548` / `辽宁 1642` / `吉林 1135` / `黑龙江 677` / `上海 6915` 亿元。
表头 `单位：亿元人民币` → `unit=亿元` 由表头实读（非写死）。

**`limit` 生效实测**：`limit=1/3/5/12 → 1/3/5/12 行`；`limit=0 → []`；非整数 → `ValueError`。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `indicator` | 指标名（规则表） | 见上三件；失败即无该行 |
| `period` | 标题年份 + 月/季/半年 | `YYYY-MM` / `YYYYQn` / `YYYYHn` / `YYYY` |
| `value` | 正文或 xlsx 单元格 | float；**未命中即无行，绝不用 0/占位填充** |
| `unit` | 源文单位 | `家` / `亿元` / `万亿元`；地区社融由表头「单位：」实读 |
| `category` | 标题分类 | `小额贷款公司统计` / `金融统计月报` / `地区社融` |
| `region` | 固定/行标签 | 全国指标 `全国`；地区社融为省名 |
| `report_title` / `report_url` | 目录页条目 | 行级可追溯 |
| `source` | 常量 | `pboc-inclusive` |
| `scraped_at` | 本地 | UTC ISO8601 |

## 坑位与容错（侦察实测，逐条落实）

1. **必须做条目发现，绝不硬编码单篇 docid**。`5885724` 只是当季样例，下季度就失效。
   本单元从目录页解析链接 + 分页回溯（`MAX_LISTING_PAGES=6`，够覆盖近 12 个月 +
   最近 2 个季度），三件**轮询交错**取（否则小 `limit` 会被第一类吃满、饿死后两类）。
2. **UA / TLS 指纹敏感**：裸 UA 可能 302 或被拦。传输统一走
   `FetcherSession(impersonate="chrome")`（实测稳定 200），`scrapling` 不可用时
   降级带 Chrome UA 的 `urllib`（实测亦可 200，但属降级）。
3. **数字带千分位逗号**（如 `1,162,939`）：`_num()` 剥离半角 `,` 与全角 `，`。
   百分比与绝对值严格区分——**时期流量只取绝对值**（`增加/减少` 方向不入 `value`，
   避免伪精确），时点余额原样取。
4. **页面导航噪音多**：正文定位到 `<div id="zoom">` 并做 **div 配平闭合**，
   不用全文正则（否则被导航/附表标题/注脚污染）。
5. **连字符目录不是合法 Python 标识符**：自检不能用
   `from spiders.pboc_inclusive.spider import ...`（必 `ModuleNotFoundError`，
   存量单元同样如此）。真实装载走 `fd_industry_data/loader.py:load_spider_module(src, content_dir)`，
   `content_dir` 传 `spiders`（不是 `.`）。
6. **地区社融正文只有一行「附件：…xlsx」**，数值全在附表：二段取数。xlsx 是 zip+xml，
   只交标准库 `zipfile` + `xml.etree`；Excel 会省略空单元格，故按 cell `r` ref 对齐列。
7. **容错**：单篇解析失败 → 跳过 + 留痕，不阻塞其他报告（实测注入 404 与无年份标题
   两篇坏报告，其余报告照常出数）；网络失败 → 记录后跳过，**不连环重试、不绕过反爬**
   （`retries=1`，单次尝试即放弃）；指标抽不到 → 整篇跳过，绝不用 0 填充；
   全部失败 → 抛 `RuntimeError`（失败即红，不外泄静默空表）。

## 历史深度

目录实测 38 页 / 约 760 条（可回溯至 2015 年附近）。本单元默认回溯 6 页
（≈120 条，覆盖近 12 个月 + 最近 2 季度）；需更深扩 `MAX_LISTING_PAGES` 即可，同构解析。

## golden 断言口径（跨期稳定）

`golden/001-pboc-inclusive.json`（`limit=60`）锚**已公布即冻结**的历史期值：
`小额贷款公司机构数 = 4863.0 家`、`小额贷款公司贷款余额 = 7229.0 亿元`
（2025Q3 小贷季报，该期已发布不会改写）；另锚 `source` / `category` /
`indicator` / `unit=万亿元` 结构位与 `min_rows: 3`。
**绝不锚当月新公布值与 `scraped_at`**（`scraped_at` 进 `whitelist_fields`）。
注意：小贷季报随季度推进会换 docid——正因为走条目发现，锚**值**而非锚**URL**，
4863/7229 属于 2025Q3 已冻结事实；未来若目录回溯窗口把该期移出，需按口径刷新锚
（正常维护，非故障）。

## 自检

```bash
cd /Users/chengsishi/finddata/fd-industry-data
python3 -m py_compile spiders/pboc-inclusive/spider.py
python3 scripts/validate_manifests.py
python3 scripts/conformance_gate.py --roots .
python3 -c "
import sys; sys.path.insert(0,'.')
from fd_industry_data.loader import load_spider_module
m=load_spider_module('pboc-inclusive','spiders')
r=m.run_pboc_inclusive(limit=20)
print('rows:',len(r))
for x in r[:5]: print({k:x.get(k) for k in ('indicator','period','value','unit','category')})
"
python3 spiders/pboc-inclusive/spider.py          # 冒烟：取 8 行打印
```

另有 golden 重放（health guard）：

```bash
python3 -c "
import sys; sys.path.insert(0,'.')
from pathlib import Path
from fd_industry_data.health import samples, guard
d=Path('spiders/pboc-inclusive').resolve()
for doc in samples.load_samples(d):
    print(guard.replay_sample(d, doc))
"
```
