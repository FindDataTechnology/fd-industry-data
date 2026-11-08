# usda-wasde —— USDA 世界农产品供需预测（WASDE，月度）

新源接入档案（`templates/new-source/` 范式）。
数据源：USDA Office of the Chief Economist
`https://usda.gov/oce/commodity/wasde/`（实测 2026-11-08，海外直连）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 索引 | `GET https://usda.gov/oce/commodity/wasde/` | 302 到 `/about-usda/.../wasde-report`，HTML 内直接给出当期与下期的 `.pdf/.txt/.xls/.xml` 直链 |
| 正文 | `GET https://www.usda.gov/oce/commodity/wasde/wasde{MM}{YY}.txt` | 定宽纯文本，实测 122KB |

**只取 `.txt`，不取 `.xls`**：定宽文本用标准库就能稳定解析，既不需要 xls 依赖，
也不会把道歉页当工作簿打开。期号与发布月从首行
`WASDE - 676 - 8    October 2026` 取。

入口：`run_usda_wasde(limit=200, window_start=None, window_end=None, months_back=6) -> list[dict]`
（`spider.py`）。仅标准库 `urllib` + `re` + `datetime`，无新依赖、不 OCR。

## 鉴权与参数

- 无 key / 无 cookie；带浏览器 UA 与 `Referer: https://usda.gov/oce/commodity/wasde/`。
  注：直链走 `/sites/default/files/documents/...` 会被 Akamai **403**，必须用索引页给的
  `https://www.usda.gov/oce/commodity/wasde/wasdeXXXX.txt` 形态。
- **禁代理**：opener 显式挂空 `ProxyHandler({})`（本机代理不可用，海外直连反而通）。
- `months_back`（默认 6）：按「最近 N 个自然月」构造候选 URL，遇 404 / 道歉页即跳过。
- `window_start`/`window_end`（`YYYY-MM`）按**发布月**过滤，先过滤后截断；不成对 → `ValueError`。

## 最关键坑：未发布期是 200 + 道歉页

未发布期返回 **HTTP 200 + 369 字节 HTML**，内容跳
`https://www.usda.gov/please_wait/apology.htm`（文案 "This file is not yet available"）。
**只看状态码会把错误页当数据。** 本单元三重判定，任一不满足即判「本期未发布」并计数：

1. `Content-Type` 必须含 `text/plain`（实测道歉页是 `text/html`）；
2. 响应体 ≥ `MIN_BODY_BYTES`（5000；正文 122KB vs 道歉页 369B）；
3. 正文必须匹配 `WASDE - <n> - ` 期号行。

实测：`wasde1126.txt` 当时即 200 + 369B → 正确判为未发布（4 个候选被跳过）。

## 解析范围与边界

只解析三类**汇总表**：`World and U.S. Supply and Use` for **Grains / Cotton / Oilseeds**。

- 表边界靠**分隔线计数**界定：标题后第 2 条 `====` 进入数据区，数据区再遇 `====` 即本节结束。
  否则会漏进后续「分品种/分国别」表，把 `Beef` / `Argentina` / `Region Beginning Produc-`
  这类表头当成品类与地区（该缺陷已实测出现并修掉）。
- **品类标签的脚注必须先剥再判数字**：`Total Grains 4/`、`Coarse Grains 5/` 带脚注数字，
  若先判「含数字即非标签」会把整块行错挂到上一品类名下（同样实测出现过）。
- **地区用白名单判定**（World / United States / Foreign）：棉花表的 `World` 反而顶格，
  靠缩进判会误当品类。
- 分品种/分国别明细表（`World Wheat Supply and Use` 等，含 40+ 国名）**不解析**，
  形态不同且列数不一致；需要时另立单元。

## 字段口径

| 列 | 口径 |
|---|---|
| `wasde_no` | 期号（676） |
| `release_month` | 发布月 `YYYY-MM`（676 → 2026-10） |
| `section` | Grains / Cotton / Oilseeds |
| `region` | World / United States / Foreign |
| `commodity` | 原文品类名（Total Grains / Wheat / Coarse Grains / Rice, milled / Cotton / Oilseeds / Oilmeals / Vegetable Oils） |
| `season` | `2024/25` 等 |
| `basis` | Actual / Estimate / Projection（取自季标签后缀 `(Est.)` / `(Proj.)`） |
| `forecast_month` | 预测行的月份 `Sep` / `Oct`；actual/estimate 行为 null |
| `item` | output / supply / trade / total_use / ending_stocks（USDA 固定 5 列次序） |
| `value` | 该节单位下的数值；`-` / `—` **不出行，绝不补 0** |
| `unit` | `Million Metric Tons`（Grains/Oilseeds）/ `Million 480-lb. Bales`（Cotton） |

**保留预测月不覆盖**：同一季 `2026/27` 的 Sep 与 Oct 两行并存（`forecast_month` 区分），
这样「本月相对上月的修正」可直接对比，而不是把上月值覆盖掉。

## 历史深度（如实声明，短窗）

- 索引页只挂**当前期与下一期**；历史归档入口
  `/oce/commodity-markets/wasde/historical-wasde-report-data` 实测 **403**（Akamai）。
- `wasde0926/1026.txt` 可取，`wasde0826/0726/0626.txt` 均 **404**。
- 因此本单元**当前真实可回溯窗口 = 最近 2 期**（675、676），属**短窗源**：
  按需声明，不假装有长序列；上线后逐月自然累积。

## 真实样例（2026-11-08 实跑，WASDE-676）

```json
{"wasde_no": 676, "release_month": "2026-10", "section": "Grains",
 "region": "World", "commodity": "Total Grains", "season": "2026/27",
 "basis": "Projection", "forecast_month": "Oct", "item": "ending_stocks",
 "value": 787.84, "unit": "Million Metric Tons",
 "source_url": "https://www.usda.gov/oce/commodity/wasde/wasde1026.txt"}
```

对照原文：世界总谷物 2024/25 产量 2858.26、2025/26 估计 3018.84、2026/27 十月预测
2944.32（九月 2944.15）；世界棉花 2024/25 产量 119.55 百万包。

## 自检

```bash
cd fd-industry-data
.venv/bin/python scripts/validate_manifests.py
.venv/bin/python scripts/conformance_gate.py
.venv/bin/python -m py_compile spiders/usda-wasde/spider.py
```

golden `001-usda-wasde.json` 用窗口钉 `2026-10`（WASDE-676），锚世界总谷物与世界棉花
历史季值；用窗口而非 `limit` 定位，新期发布不会把锚点挤出。
