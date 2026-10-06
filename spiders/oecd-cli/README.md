# oecd-cli —— OECD 复合先行指标（CLI）

新源接入档案。工单：`reports/health-tickets/20261006-oecd-cli-ac318af6.yaml`
（`kind=generate`，brief 侦察簿 W1-A 深潜）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 序列取数 | `GET https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI/<key>?format=csvfilewithlabels` | 公开 SDMX 2.1 REST，**免 key**，CSV 导出 |

入口：`run_oecd_cli(limit=100, key=None) -> list[dict]`（`spider.py`）。
仅用标准库 `urllib` + `csv`，无新依赖。

## key 口径与 URL 形态（与 brief 的偏差修正，实测证据）

- dataflow = `OECD.SDD.STES:DSD_STES@DF_CLI`（STES 短期经济统计，DF_CLI = CLI 流）。
- **key 必须与 dataflow 用 `/` 分隔**（SDMX 2.1 REST `data/<flowRef>/<key>` 形态）。
  brief `source_urls` 用逗号连写（`...DF_CLI,CHN.M...`），实测一律报
  `Invalid version string provided`——逗号把 key 顶进了 flowRef 的 version 位。
- 种子 key `CHN.M.LI.IX._Z.NOR.IX._Z.H`（9 段；中国 CLI、月度、指数、季调归一），
  实测返回 412 个月度 obs（1992-05 至 2026-08）。可扩各国：换首段 `REF_AREA`。
- **CSV 导出必须 `format=csvfilewithlabels`**（码值+标签双列；实测 `csvfile` 等其他
  形态未采用，仅用 brief 点名的这一个）。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `period` | `TIME_PERIOD` | 月期格式（如 `2020-01`） |
| `value` | `OBS_VALUE` | CLI 归一指数值，**round 6 位**归一（如 `96.25574`） |
| `series_key` | 入参 | 完整 9 段 key，行级可追溯 |
| `ref_area` / `freq` / `measure` / `unit_measure` / `adjustment` / `transformation` | CSV 同名码值列 | 序列维度上下文（CHN / M / LI / IX / NOR / IX） |
| `url` | 请求 URL | 行级来源可追溯 |
| `source` | 常量 | `oecd-cli` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 坑位与容错（brief.notes 逐条落实）

- **上游行不按期次排序**（实测 CSV 首行 2012-09、末行 2012-08）——必须按
  `TIME_PERIOD` 升序排序后再截 `limit`，否则「最近 N 行」语义错误。
- **空响应**（错 key 时 OECD 返回错误文本/空体）：解析后无 `TIME_PERIOD` 列或零数据
  行 → 该 key 失败并留痕；**全部 key 失败抛 `RuntimeError`**（失败即红，不静默返回
  空列表，避免「空洞通过」）。
- **`limit` 语义**：行数上限，升序后保留**最近** `limit` 期；golden 用小 `limit` 锚
  固定历史月值 → 重放恒定。
- 免 key；但站点拒绝缺省 Python UA（实测 `Python-urllib/3.x` → HTTP 403），本单元只带
  常规浏览器 UA（nmc-weather 同款固定 UA），不做指纹伪装与频次对抗，遇风控升级按协议
  转人工，不逆向。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 历史深度与节奏

种子序列 1992-05 起（412 个月 obs，2026-10 实测），月度更新（次月末发布当月值，
CLI 为预估值、近期月份可能修订——golden 只锚 2020 年深历史值）。

## 实测样例行与证据（2026-10-07 抓取）

```
TIME_PERIOD=2020-01, OBS_VALUE=96.25574, MEASURE=LI(Composite leading indicator),
UNIT_MEASURE=IX(Index), ADJUSTMENT=NOR(Normalized)
```
→ `{"period": "2020-01", "value": 96.25574, "series_key": "CHN.M.LI.IX._Z.NOR.IX._Z.H", ...}`

## 自检

```bash
python3 spiders/oecd-cli/spider.py          # 冒烟，打印最近 3 行 obs
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-oecd-cli-ac318af6.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-oecd-cli.json` 锚**固定历史月值**：`period == 2020-01` 及其
`value == 96.25574`，外加常量标签 `series_key` / `source` 与 `min_rows: 100`。
**绝不锚**最近月份（CLI 近期值随每次发布修订）；`scraped_at`/`timestamp` 进
`whitelist_fields`。若 OECD 未来对 2020-01 归一重算导致锚红，属上游口径变更，
按 sample-update-pending-human 走人工复锚，不静默放水。
