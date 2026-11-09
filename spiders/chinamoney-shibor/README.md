# chinamoney-shibor —— 中国货币网 Shibor 八期限 + FR/FDR 六期限（全史日频）

新源接入档案（批次六·银行业，change `add-banking-industry-sources` 任务 3.1）。

## 端点（从站点前端 fragment 实读）

| 通道 | 端点 | 方法 | 参数 |
|---|---|---|---|
| Shibor 八期限 | `https://www.chinamoney.com.cn/ags/ms/cm-u-bk-shibor/ShiborHis` | **POST**（空 body） | `lang=CN&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD` |
| FR / FDR 定盘 | `https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/FrrHis` | POST（空 body） | 同上 |

`ShiborHis` **必须 POST**——用 GET 打同一 URL 拿不到 `records`（侦察簿坑位，本轮复核成立）。
FR/FDR 的期限键来自响应的 `frValueMap`：`FR001/FR007/FR014` 与 `FDR001/FDR007/FDR014`；
Shibor 的行键是 `ON/1W/2W/1M/3M/6M/9M/1Y`，输出统一成 `ShiborO/N`…`Shibor1Y`。

## 三个硬坑

1. **行在响应顶层 `records`**，不是 `data.records`。读 `data.records` 会得到 0 行
   （`data` 只放元信息）。这是本单元最贵的一个坑：按 `data.records` 试了多种参数
   都是 0，直到读站点 fragment JS（`fillTableShiborHis` 用 `data['records']`，
   列定义取 `data['baseCurveCfgList']`）才定位。
2. **假 200 嗅探**：上游会先回 HTTP 200 + HTML 错误页（不是 JSON）。本单元嗅探
   条件 = 响应能解析为 JSON **且** 顶层存在 `records`；不满足即视为失败，
   退避 5s 重试**一次**，仍不过则 `RuntimeError`（失败即红，绝不静默回空）。
3. **窗口必须 ≤1 年**：长区间（如 2024-01-01..2026-11-07）实测返回空。
   本单元从 `FIRST_DATE = 2006-10-08` 起**按年分片**，每片 ≤1 年。

## 取数纪律

- 同站请求间隔 `INTERVAL_SECONDS = 3.5`（Shibor 站点限频严，侦察簿要求 ≥3–5s）；
- 每片独立 try/except：单片失败 stderr 留痕并续跑，不拖垮其余分片；
- 两通道**全部**分片失败、合计 0 行 → `RuntimeError`；
- `limit` 上限 `LIMIT_CAP = 200_000`（全史约 7 万行，留裕量）；
  传大值即全史回补。

## 首爬回补窗口（`window_start` / `window_end`）与 `days` 默认窗口

全史 20 年按年分片 ≈140 个请求 / 157s。`runner_cli` 只在 `--limit`/`--params`/
窗口参数上给入口传参，**不会注入 manifest 里的默认值**——若入口默认抓全史，
日频调度每天都会重打同一组请求指纹，正是本单元 README 记的 403 封禁成因。
因此入口把日频默认收敛为增量，回补走显式路径：

```python
run_chinamoney_shibor(limit=200, days=45, window_start=None, window_end=None)
```

优先级：**平台窗口 > `days`**。

| 路径 | 抓取范围 | 请求数（实测） |
|---|---|---|
| 日频默认（`days=45`） | 近 45 日历日，落在 1 个年分片 | **2**（7.4s，70 行） |
| `days=0` | 全史 2006-10-08 起 | ≈140（156s） |
| 平台窗口（首爬回补） | 窗口内年分片 | 随窗口（`2026-11-01..11-08` = 2） |

- **未带窗口且 `days=0`** = 全史抓取，行为与接入时完全一致（golden 用这条路径
  验 2006-10-08 锚）；
- **带窗口**（dispatcher 首爬/补数经 runner 注入，YYYY-MM-DD，成对）= 只抓与窗口
  相交的年分片。每片仍是自然年的子区间，故**仍满足上游 ≤1 年硬限**；
- 窗口内全是休市/未定盘日（上游合法返回空 `records`）→ **回空 `[]` 并 stderr
  留痕**，不抛错——这不是上游故障，不该让平台记假红；
- 窗口不成对 / 非 `YYYY-MM-DD` / 起晚于止 / `days` 非整数或为负 → `ValueError`
  （带窗口的运行必须响亮失败，不能静默取错区间）。

窗口参数**不上 manifest 声明面**：它是平台属有参数，走专用 flag，与 `limit` 同类
（同 `spiders/abs-sdmx` 既有惯例）；`days` 是本单元自有声明参数（manifest 已列）。

## 双锚口径（为什么不是「只返回最近 N 行」）

tasks.md 3.1 要求「golden 锚=2006-10-08 首日值（ON 2.1184 等）与最新交易日双锚」。
若入口只返回最近 `limit` 行，首日锚永远落在窗口外，golden 无法验证
「全史从 2006-10-08 起可回补」这件事实。因此入口的返回口径是：

```
最近 limit 行（date desc） + 2006-10-08 首交易日锚行（按 (date, curve) 去重）
```

这样一次运行同时证明：① 序列起点正确；② 最新交易日可取。
`limit` 传大值即退化为纯全史（锚行本来就在里面，去重后不重复）。

带窗口时「起点锚」= 窗口内最早定盘日（窗口外的首交易日不在本次输出里），
`limit` 同时生效——即「窗口内最近 limit 行 + 窗口内首行」。

## 实测取数证据

| 运行 | 参数 | 结果 |
|---|---|---|
| 首爬（2026-11-07） | `limit=20` | 返回 20 行 + 首日锚行；Shibor 八期限与 FR/FDR 六期限均有值 |
| golden 重放 | `limit=20` | `ok=True`，`diffs=[]`；锚 `date=2006-10-08` / `curve=ShiborO/N` / `value=2.1184` 全部命中 |
| 窗口增量（2026-11-08） | `window 2026-11-01..2026-11-08` | 2 个请求：ShiborHis 40 行 + FrrHis 30 行（全年片不再抓） |

首日锚（2006-10-08）实测值：`ShiborO/N = 2.1184`（其余七期限同日亦有值）。
`value` 为百分数数值（2.1184 = 2.1184%），`unit` 列恒为 `"%"`；
上游 `---` / `null` 视为缺测**不出行**。

## golden 口径

`golden/001-chinamoney-shibor.json`：`params {"limit": 20}`，`min_rows 8`，
锚 `source` / `curve=ShiborO/N` / `date=2006-10-08` / `value=2.1184`
（`rows_contains` 各条独立满足，故不要求同一行同时满足四项）。
首日值是**历史常量**，不会随每日定盘漂移——这正是选它做锚的原因；
最新交易日的活性由 `min_rows` 与双锚返回口径共同保证（若上游停更，
返回行数会塌到只剩锚行，`min_rows 8` 立刻红）。

## 失败策略

- 单片失败 → stderr 留痕续跑（节假日/临时故障不中断全史）；
- 假 200 → 退避 5s 重试一次；
- 全部分片 0 行 → `RuntimeError`（不许"看起来成功但没数据"）；
- `limit` 非法（非 int）→ `ValueError`。
