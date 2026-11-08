# eastmoney-disclosure-plan —— 东财定期报告预约披露日历

自建单元（2026-11-07，批次六·银行业数据线）。抓东方财富数据中心的**定期报告
预约披露日历**（首次预约日 / 变更日 / 实际披露日），作为银行定期报告
**「到期未披露」预警**的数据源。免 key、免鉴权、常规 UA 直连，manifest 不声明
`egress_secret`、不硬编码代理地址。

## 1. 数据表面

| 项 | 值 |
|---|---|
| 落地页 | https://data.eastmoney.com/bbsj/ （东财「年报季报」频道） |
| JSON 端点 | `GET https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_PUBLIC_BS_APPOIN&columns=ALL&pageSize=500&pageNumber=1&sortColumns=SECURITY_CODE&sortTypes=1&filter=<整体 urlencode 后的表达式>` |
| 鉴权/请求头 | 无。常规 Chrome UA + `Accept: application/json` |
| 宇宙筛选 | `in` 批量：`(SECURITY_CODE in ("601398","601288",...))`；代码来自注册表 `universes/cn-a.yaml`（`verticals: [bank]` 共 42 家），不在 `spider.py` 内嵌 |
| 时间窗筛选 | `(FIRST_APPOINT_DATE>='2025-01-01')`；两个条件**并列括号拼接**（无 `and`） |
| 分页 | `pageSize=500`（实测上限生效），按响应 `result.pages` 翻页；2007 至今全史 2,080 行 = 5 页 |
| 排序 | 上游按 `SECURITY_CODE` 升序（分页稳定，实测 5 页 2,080 行无重复/缺失）；输出本地按 `first_appoint_date` 倒序 |
| 频度/深度 | 随披露滚动更新；上游最早 `FIRST_APPOINT_DATE=2007-03-14`（「回溯 2007」实锤） |
| 响应形态 | `{success:true, code:0, message:"ok", result:{count, pages, data:[...]}}`；0 结果时 `result:null` |

上游字段（`columns=ALL` 实测返回 29 列，本单元取 8 个）：

| 上游字段 | 含义 | 输出键 |
|---|---|---|
| `SECURITY_CODE` | 股票代码 | `stock_code` |
| `SECURITY_NAME_ABBR` | 简称 | `stock_name` |
| `REPORT_TYPE` | **1=一季报 2=半年报 3=三季报 4=年报** | `report_type`（中文名） |
| `REPORT_YEAR` | 报告年度 | `fiscal_year`（int） |
| `FIRST_APPOINT_DATE` | 首次预约披露日 | `first_appoint_date` |
| `FIRST/SECOND/THIRD_CHANGE_DATE` | 逐次变更日 | `change_date`（取最新非空） |
| `APPOINT_CHANGE` | 变更方向（提前/延后，可空） | 参与 `plan_status` 派生 |
| `ACTUAL_PUBLISH_DATE` | 实际披露日（可空） | `actual_publish_date`（保留 null） |

输出行（10 键，与 manifest `columns` 一一对应）：

```json
{"stock_code":"601398","stock_name":"工商银行","report_type":"年报","fiscal_year":2025,
 "first_appoint_date":"2026-03-31","change_date":"2026-03-28","actual_publish_date":"2026-03-28",
 "plan_status":"已披露","source":"eastmoney-disclosure-plan","scraped_at":"2026-11-07T14:56:36+00:00"}
```

`plan_status` 本地派生（顺序敏感）：`actual_publish_date` 非空 → **已披露**；
否则存在变更日或 `APPOINT_CHANGE` → **已变更**；否则 → **预约中**。

`limit`（默认 200）真实生效：首爬窗口 `FIRST_APPOINT_DATE >= 2025-01-01`
（2026-11-07 实测 336 行）；若窗口行数 < `limit`，自动放宽到 2007 全史
（2,080 行），上限 `LIMIT_CAP=2080`。按 `first_appoint_date` 倒序，同日按
`stock_code, report_type` 升序，确定性可复现。

## 2. A 股银行清单（42 家）

代码登记在仓库根 `universes/cn-a.yaml`（`verticals: [bank]`，33 沪 + 9 深），来源：侦察簿。`spider.py` 不再内嵌清单，缺省按 `DEFAULT_VERTICALS=("bank",)` 从注册表过滤；其它垂直线（酒店/餐饮）以 `codes` 参数显式传入自己的 A 股宇宙。
`data-source-scouting/INDUSTRY-DATA-SOURCES.md` 第 791 行「名单核实」——
东财行业板块 BK0475（银行Ⅱ）`total=42`，2026-11-07 复核 42/42 全部命中本
端点（42 个代码全史 2,080 行）。清单：工商 601398 / 农业 601288 / 中国
601988 / 建设 601939 / 交通 601328 / 邮储 601658 / 招商 600036 / 兴业
601166 / 中信 601998 / 民生 600016 / 光大 601818 / 浦发 600000 / 华夏
600015 / 浙商 601916 / 成都 601838 / 紫金 601860 / 苏农 603323 / 江苏
600919 / 杭州 600926 / 西安 600928 / 无锡 600908 / 南京 601009 / 北京
601169 / 厦门 601187 / 上海 601229 / 常熟 601128 / 渝农商 601077 / 沪农商
601825 / 贵阳 601997 / 重庆 601963 / 齐鲁 601665 / 长沙 601577 / 瑞丰
601528 / 宁波 002142 / 平安 000001 / 兰州 001227 / 江阴 002807 / 张家港行
002839 / 苏州 002966 / 青岛 002948 / 郑州 002936 / 青农商行 002958。
**生产口径以工具实时拉取（BK0475 成分）为准，本清单是内嵌筛选白名单**；
银行上市/退市变动时须同步更新代码清单（否则名单漂移静默漏行）。

## 3. filter 参数必须整体 urlencode —— 头号坑（醒目）

**`filter` 表达式必须整体编码**：`urllib.parse.urlencode({"filter": expr})`
（本单元把整条 query dict 一起 `urlencode`，filter 被整体编码）。**不要**
手工拼接、不要逐段编码、不要只编码引号/只编码空格。

2026-11-07 本机复测的编码形态与服务器行为：

| 编码形态 | 结果 |
|---|---|
| A. 整条 query `urlencode`（本单元实现） | **HTTP 200 + JSON**，`success:true, code:0`，count=80（单行代码探针） |
| B. `quote(expr, safe="()")`（空格→`%20`，括号留原样） | HTTP 200 + JSON，count=80（可接受，但仍非「整体编码」口径） |
| C. `quote_plus(expr, safe="()")`（空格→`+`） | HTTP 200 + JSON，count=80 |
| D. 表达式原样拼进 URL（空格未编码） | **客户端即失败**：`InvalidURL: URL can't contain control characters` |
| E. 空格编码但**引号留原样**（`filter=(SECURITY_CODE%20in%20("601398"))`） | **HTTP 400 Bad Request** |

侦察簿原文（路 7 坑位，2026-10）：编码不全时会出现**非 JSON 响应 / 连接重置**。
本日复测的失败面是 400 与客户端拒发；无论表现如何，结论一致：**一律整体
urlencode**。`spider.py::_build_url` 用 `urlencode(query)`，filter 值由
`_build_filter` 一次生成、不二次编码。

## 4. 实测证据（2026-11-07，本机 macOS 直连，无代理）

| 请求 | 结果 |
|---|---|
| `GET datacenter-web.eastmoney.com/api/data/v1/get?...RPT_PUBLIC_BS_APPOIN&filter=(SECURITY_CODE="601398")(REPORT_TYPE="4")(REPORT_YEAR="2025")` | **HTTP 200**，`success:true/code:0`，count=1 —— 工行 FY2025 年报 |
| 同上 42 家 `in` 全史（pageSize=500，5 页） | **HTTP 200**，count=2,080，5 页实取 2,080 行；42/42 代码命中；最早 `FIRST_APPOINT_DATE=2007-03-14` |
| 42 家 `in` + `(FIRST_APPOINT_DATE>='2025-01-01')` | **HTTP 200**，count=336（单页） |
| `GET https://data.eastmoney.com/bbsj/`（落地页） | **HTTP 200** |
| `run_eastmoney_disclosure_plan(limit=200)` 实跑 | **200 行**；首行 FAD `2026-10-31`，末行 FAD `2025-10-29`；含 golden 锚行 |
| `run_eastmoney_disclosure_plan(limit=400)` 实跑 | **400 行**（窗口 336 < 400 → 自动放宽全史），末行 FAD `2024-08-29` |

**工行 FY2025 年报三元组实取值**（golden 锚，逐项对账一致）：

| 字段 | 上游实取 | 输出 |
|---|---|---|
| 首次预约 | `FIRST_APPOINT_DATE = 2026-03-31 00:00:00` | `first_appoint_date = 2026-03-31` |
| 变更 | `FIRST_CHANGE_DATE = 2026-03-28 00:00:00`（`APPOINT_CHANGE=提前`，3 天） | `change_date = 2026-03-28` |
| 实际披露 | `ACTUAL_PUBLISH_DATE = 2026-03-28 00:00:00` | `actual_publish_date = 2026-03-28` |
| 状态 | `IS_PUBLISH=1` | `plan_status = 已披露` |

行数口径校验：截断时 `len(rows) != result.count` 即抛 `RuntimeError`（翻页期间
上游变更时重跑即可，不静默返回残缺数据）。`scraped_at` 每次抓取为同一批次
时间戳（UTC ISO）。

## 5. 坑位（逐条）

1. **`filter` 必须整体 urlencode**（§3）：引号原样 → HTTP 400；空格原样 →
   客户端 `InvalidURL`；侦察簿记录的不全编码还会非 JSON/连接重置。
2. **条件之间不能用 `and`**：`(A and B)` 语法实测返回 `result:null`（0 行、
   不报错）——静默空结果。多个条件必须**并列括号拼接**：`(A)(B)`。本单元
   `_build_filter` 只做拼接，不做 `and`。
3. **`REPORT_YEAR` 比较符静默失效**：`(REPORT_YEAR>='2025')` 实测被忽略，
   count 仍 2080（返回全量）。窗口过滤一律用**日期字段**
   `FIRST_APPOINT_DATE>='YYYY-MM-DD'`（实测生效）；若必须按年过滤，用
   `in ("2025","2026")` 显式枚举。
4. **`REPORT_TYPE` 是字符串枚举**（`"1"`..`"4"`，不是名称）：未知值即抛
   `RuntimeError`（上游新增报告类型须人工映射），绝不猜测中文名。
5. **全史当前全部已披露**：2007 至今 2,080 行 `IS_PUBLISH=1`、
   `ACTUAL_PUBLISH_DATE` 无空值——「预约中/已变更」状态只会在上游发布未来
   预约后出现；状态派生逻辑已就位，勿以当前分布推断逻辑失效。
6. **`change_date` 取最新非空**：`FIRST/SECOND/THIRD_CHANGE_DATE` 逐次记录，
   直接取 FIRST 会在二次/三次改期时给出过期日期。`APPOINT_CHANGE` 可单独
   存在（方向标记），参与 `已变更` 判定。
7. **日期统一 `YYYY-MM-DD`**：上游带 ` 00:00:00` 后缀，`_iso_date` 截前 10
   位并校验形态；形态漂移即抛错。
8. **`result:null` 是合法空结果**：表达式语法不被识别（如 `and`）或确实 0
   行都返回 `result:null`；本单元在 0 行时抛 `RuntimeError`（失败即红），
   不做「空也成功」。
9. **分页一致性**：`count` 与实际行数不等即抛错；`pageSize=500` 实测上限
   生效，无需试探更大值。
10. **单请求单尝试**：每页 1 次 GET、不重试、不加频次对抗；仅常规 UA，遇
    风控升级按协议转人工。
11. **失败策略**：HTTP 错误 / 非 JSON / `success!=true` / `code!=0` /
    缺 `result` 结构 / 记录缺必需字段（报错带实际键清单）/ 未知
    `REPORT_TYPE` / 分页不一致 / 0 观测行 → `RuntimeError`，绝不静默回空。

## 6. golden 断言口径

`golden/001-eastmoney-disclosure-plan.json` 以 `{"limit": 200}` 重放：

- `min_rows: 2`；
- `rows_contains` 只锚**已封档的历史事实**（工行 FY2025 年报已完成披露，
  日期不再变动）：`source`、`stock_code=601398`、`fiscal_year=2025`、
  `report_type=年报`、`first_appoint_date=2026-03-31`、
  `actual_publish_date=2026-03-28`；
- `scraped_at` 进 `whitelist_fields`。

风险提示：`limit=200` 的倒序窗口会随新预约滚动。若未来 42 家银行的更新
预约把工行该行挤出前 200（当前 200 行末位 FAD=2025-10-29，工行锚行
FAD=2026-03-31 在窗口内仍有余量），golden 需要把 `limit` 调大（上限 2080）
或改为按更早 `limit` 锚定；本行是**已披露历史**，不会因改期而变。

## 7. 消费方注记（先入座后接线）

- **C 预警线（到期未披露巡检）**：用 `plan_status in ("预约中","已变更")` +
  `first_appoint_date` / `change_date` 与「今天」比较——计划披露日已过且
  `actual_publish_date is null` → 到期未披露告警。注意 `change_date` 优先于
  `first_appoint_date` 作为到期基准。
- **B 财报线（cn-report ingest）**：`plan_status=="已披露"` 且
  `actual_publish_date` 到达即为财报入库触发信号（与 `cn-report-incremental-crawl`
  水位线配合；industry 批次只提供日历，不重复接财报，见侦察簿裁决记录 2）。
- 数据是**预约日历**，不是财报内容；`source="eastmoney-disclosure-plan"`
  用于与巨潮公告通道（cn-report 车道）区分与对账。

## 8. 自检

```bash
cd /Users/chengsishi/finddata/fd-industry-data
.venv/bin/python spiders/eastmoney-disclosure-plan/spider.py     # 冒烟：打印最近 5 行
.venv/bin/python scripts/validate_manifests.py
.venv/bin/python scripts/check_manifest_commands.py
.venv/bin/python scripts/conformance_gate.py --roots .
.venv/bin/python - <<'PY'
from pathlib import Path
from fd_industry_data.health import guard, samples
d = Path("spiders/eastmoney-disclosure-plan"); s = samples.load_samples(d)[0]
print("schema:", samples.validate_sample(s) or "OK")
print("replay:", guard.replay_sample(d, s))
PY
```
