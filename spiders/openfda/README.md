# openfda —— openFDA 药物不良事件月度量

新源接入档案（`templates/new-source/`）。工单：`reports/health-tickets/20261006-openfda-56bbec7f.yaml`
（`kind=generate`，批次二，侦察簿 W2-2）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 月度量 | `GET https://api.fda.gov/drug/event.json?search=receivedate:%5B<起>+TO+<止>%5D&limit=1` | 公开 REST，**免 key**（240/min），`meta.results.total` = 区间事件总量，`results[0]` = 抽样明细 |

入口：`run_openfda(limit=24, start_month=None, end_month=None) -> list[dict]`（`spider.py`）。
仅用标准库 `urllib`，无新依赖。审批/器械端点同构可扩（本单元只接 `drug/event`）。

## 参数白名单（只发实测过的参数）

`search`（`receivedate:[yyyy-mm-dd TO yyyy-mm-dd]` 方括号区间）+ `limit=1` —— 其余不发。

## 口径坑位（实测证据）

- **区间必须有方括号**：工单字面 URL `search=receivedate:2020-01-01+TO+2020-01-31`
  实测返回 13,943,853（≈全库，`TO` 被当噪音静默忽略）；正确写法
  `receivedate:%5B2020-01-01+TO+2020-01-31%5D` 返回 134,689（2020-01，符合 brief
  「月度 10 万+」量级）。本单元只发方括号形态。
- **索引加载滞后**：未发布月 API 返回 404 `NOT_FOUND`——按「该月尚未发布」**跳过留痕**，
  绝不落 0 行（成熟月份必然 10 万+，真实 0 月自 2004 起不存在）。实测 2026-10-06 时
  数据止于 2026-06（116,816）。
- **缺省窗口自动探测**：从上个完整自然月起向后探测最近已发布月（深度上限 6 个月），
  以它为端点倒推 `limit` 个月；全部未发布 → `RuntimeError`。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `month` | 派生 | 自然月 ISO `yyyy-mm` |
| `event_count` | `meta.results.total` | 该月不良事件报告量（int） |
| `sample_report_id` | `results[0].safetyreportid` | 抽样明细报告 ID（索引重载可能变化，故入 whitelist） |
| `url` / `source` / `scraped_at` | 请求 URL / 常量 / 本地 | 行级溯源 |

## 历史深度与节奏

- 数据 **2004 年起**，月度 10 万+ 记录；成熟月份 total 发布后基本冻结。
- `frequency: monthly`；缺省取最近 `limit` 个**已发布**月，历史由调度正向累积。

## 容错

- 单月失败（含未发布 404）只跳过并留痕；**全部月份失败抛 `RuntimeError`**（失败即红）。
- 免 key 限 240/min：`limit=24` 仅 24+1 请求（含 1 次探测），远低于限额；不重试不加频次。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 自检

```bash
python3 spiders/openfda/spider.py          # 冒烟，打印最近 3 个已发布月
python3 scripts/check_manifest_commands.py  # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-openfda-56bbec7f.yaml
```

## 实测样例行（2026-10-06，golden 钉窗口重放）

```json
{"month": "2020-01", "event_count": 134689, "sample_report_id": "17221353",
 "url": "https://api.fda.gov/drug/event.json?search=receivedate:%5B2020-01-01+TO+2020-01-31%5D&limit=1",
 "source": "openfda", "scraped_at": "…"}
```

缺省窗口实测（同日）：2026-04=127,219 / 2026-05=108,914 / 2026-06=116,816。

## golden 断言口径（跨期稳定）

`golden/001-openfda.json` 用 `params` 钉死 2020-01..2020-02 固定历史窗口，锚
**冻结月总量**：`month == 2020-01`、`event_count == 134689`、`source`/`url` 常量、
`min_rows: 1`。**绝不锚** 近月数值（索引回填会微调）、`sample_report_id`（抽样顺序可能
变化，进 `whitelist_fields`）与 `scraped_at`/`timestamp`（在 `whitelist_fields`）。
