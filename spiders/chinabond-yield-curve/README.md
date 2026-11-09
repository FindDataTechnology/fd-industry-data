# chinabond-yield-curve —— 中债登 商业银行资本工具收益率曲线（二级资本债 / TLAC 非资本债）

新源接入档案（批次六·银行业，change `add-banking-industry-sources` 任务 4.3）。

## 端点（本轮实读打通）

| 用途 | 端点 | 方法 | 说明 |
|---|---|---|---|
| 曲线码表 | `https://yield.chinabond.com.cn/cbweb-mn/yc/queryTree?locale=zh_CN` | GET | 180 个节点，实读 `ycDefId` + 曲线名 |
| 整年包 | `https://yield.chinabond.com.cn/cbweb-mn/yc/downYearBzqx?year=<YYYY>&ycDefId=<id>` | GET | 返回整年包，每曲线 1 个请求 |

整年包响应头是 `application/x-msdownload`，**内容其实是 OOXML（xlsx）**，四列：
`日期 | 标准期限说明 | 标准期限(年) | 收益率(%)`。

## 三个硬坑

1. **Referer 必带**：不带 `Referer: https://yield.chinabond.com.cn/` 实测被拒。
   本单元在共享 `HEADERS` 里固定该头。
2. **xlsx 字符串内联**：`sharedStrings.xml` 为空，字符串直接内联在 sheet XML 里。
   **必须走共享层 `fd_industry_data.tabular.read_grid`**——单元内内联手写 xlsx
   解析会被 conformance gate 的 `inline-tabular-parser` 拦（侦察簿路 5 已记该形态）。
3. **表头行要探测**：包前面可能带标题/空行，故在前 5 行里找同时含「日期」与
   「收益率」的行做表头，找不到即该曲线判失败（`ValueError`）——不猜列序。

侦察簿路 5 另有两个**本单元绕开**的坑，留痕备查：`GetDataByte` 的 `sCode`
与页面序号差一位（按页面序号取会错表）；`searchYc` 老端点已死。本单元只用
整年包，不碰这两条路径。

## 曲线码表（`queryTree` 实读，硬编码 7 条）

| `ycDefId` | 曲线名 |
|---|---|
| `8a8b2cee67a9cd3a0167ac48fc234850` | 二级资本债(AAA-) |
| `8a8b2cee67a9cd3a0167ac533bc03f4f` | 二级资本债(AA＋) |
| `8a8b2cee67a9cd3a0167ac53aa463f51` | 二级资本债(AA) |
| `8a8b2cee67a9cd3a0167ac5407c63f53` | 二级资本债(AA-) |
| `8a8b2cee67a9cd3a0167ac54671c3f55` | 二级资本债(A＋) |
| `8a8b2cee67a9cd3a0167ac54dac83f57` | 二级资本债(A) |
| `998183fe91c707050193099291ec33b3` | 总损失吸收能力非资本债(AAA-) |

曲线名按上游**原始写法**保留（含全角 `＋`、带括号评级），下游按字符串精确匹配。
码表变化属上游口径变更，需重新实读 `queryTree` 后改本表。

## 取数纪律

- 整年包每曲线 1 个请求，共 7 个；实测连打约 6s 稳定，**因此不设请求间隔**
  （早前版本有个从未被使用的 `SLEEP` 常量，已删——留着只会让人误以为有节流）；
- 单曲线失败 stderr 留痕**续跑**其余曲线；7 条全空才 `RuntimeError`（失败即红）；
- `limit<=0` 直接返回 `[]`；`days>0` 只保留最近 N 自然日（先过滤后截断）。

## 参数

```python
run_chinabond_yield_curve(limit: int = 1200, days: int = 0)
```

- `limit`：返回行数上限（按 `(date, curve_name, tenor_years)` 倒序截断）；
- `days`：只回最近 N 自然日的点；`0` = 整年包全量（**回补口径**，默认）。

默认 `days=0` 之所以安全：整年包本身就是 7 个请求，不存在 shibor 那种
「日频重抓全史」的成本问题。

## 实测取数证据

| 运行 | 参数 | 结果 |
|---|---|---|
| 首爬 / 验收重放（2026-11-08） | `{"limit": 1200, "days": 3}` | 218 行 / 6.3s，7 条曲线全出数 |
| golden 重放 | `{"limit": 1200, "days": 3}` | `ok=True diffs=[]` |

## golden 口径

`golden/001-chinabond-yield-curve.json`：`params {"limit": 1200, "days": 3}`，
`min_rows 150`，锚为 `source` / 三条 `curve_name`（`二级资本债(AAA-)`、
`二级资本债(AA＋)`、`二级资本债(A)`）/ `总损失吸收能力非资本债(AAA-)` /
`tenor_label`（`0d`、`1y`、`10y`）/ `tenor_years=10.0` / `unit="%"`。
`rows_contains` 各条独立满足，故不要求同一行同时满足全部锚。`scraped_at` 白名单。

选 `days: 3` 而非默认 `days: 0`：golden 只验「最近交易日有数 + 曲线/期限/单位口径」，
不必拉整年包；整年包全量能力由 `days=0` 这一默认路径承担（已实跑）。

## 失败策略

- 单曲线失败 → stderr 留痕续跑（不拖垮其余 6 条）；
- 表头探测失败 / 非 xlsx → 该曲线 `ValueError`，续跑；
- 7 条全空 → `RuntimeError`（不许「看起来成功但没数据」）；
- 缺测（日期或数值缺失）不出行，不补 0。
