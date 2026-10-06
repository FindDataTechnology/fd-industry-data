# ecb-exr —— 欧央行欧元参考汇率

新源接入档案。工单：`reports/health-tickets/20261006-ecb-exr-a0aee352.yaml`
（`kind=generate`，brief 侦察簿 W1-C）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 序列取数 | `GET https://data-api.ecb.europa.eu/service/data/EXR/<series>?format=csvdata` | 公开 SDMX 2.1 REST，**免 key**，CSV 导出 |

入口：`run_ecb_exr(limit=100, series=None) -> list[dict]`（`spider.py`）。
仅用标准库 `urllib` + `csv`，无新依赖。

## series 口径

- 种子 series `M.CNY.EUR.SP00.A`（月均 CNY/EUR 即期参考汇率），实测返回 321 个月
  obs（2000-01 至 2026-09）。
- **`format=csvdata` 最顺滑**（brief 点名，实测纯码值列 CSV，解析直取
  `TIME_PERIOD` / `OBS_VALUE`）。
- **日频可扩 `EXR/D...`**（brief 点名扩展路径；本单元只实测过 M 系列入口，扩展序列
  经 `series` 参数直通，不盲发未实测参数）。工单 `cadence: daily` → manifest 频率按
  `daily` 登记（种子序列为月度，日频切档留给点亮后决策）。
- 可扩币种：换 `CURRENCY` 段（如 USD、JPY）。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `period` | `TIME_PERIOD` | 期次格式（月频 `2020-01`；日频 `YYYY-MM-DD`） |
| `value` | `OBS_VALUE` | 1 EUR 兑目标币种；上游全精度浮点（如 `7.6832363636364`）**round 6 位**归一 |
| `series_key` | 入参 | 完整 5 段 series key，行级可追溯 |
| `freq` / `currency` / `currency_denom` / `exr_type` / `exr_suffix` | CSV 同名码值列 | 序列维度上下文（M / CNY / EUR / SP00 / A） |
| `url` | 请求 URL | 行级来源可追溯 |
| `source` | 常量 | `ecb-exr` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 坑位与容错（brief.notes 逐条落实）

- **空响应**（错 series 时 ECB 返回 4xx 错误文本）：解析后无 `TIME_PERIOD` 列或零数据
  行 → 该 series 失败并留痕；**全部 series 失败抛 `RuntimeError`**（失败即红，不静默
  返回空列表，避免「空洞通过」）。
- **`limit` 语义**：行数上限，期次升序后保留**最近** `limit` 期；golden 用小 `limit`
  锚固定历史月值 → 重放恒定。
- 免 key、免 UA 伪装；不做指纹伪装与频次对抗，遇风控升级按协议转人工，不逆向。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 历史深度与节奏

种子序列 2000-01 起（321 个月 obs，2026-10 实测）；月均序列次月初定档不再修订，
日频参考汇率每日 ~16:00 CET 发布。工单节奏 `daily`。

## 实测样例行与证据（2026-10-07 抓取）

```
KEY=EXR.M.CNY.EUR.SP00.A, TIME_PERIOD=2020-01, OBS_VALUE=7.6832363636364, OBS_STATUS=A
```
→ `{"period": "2020-01", "value": 7.683236, "series_key": "M.CNY.EUR.SP00.A", ...}`

## 自检

```bash
python3 spiders/ecb-exr/spider.py          # 冒烟，打印最近 3 行 obs
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-ecb-exr-a0aee352.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-ecb-exr.json` 锚**固定历史月值**：`period == 2020-01` 及其
`value == 7.683236`（月均发布后定档，跨期不变），外加常量标签 `series_key` /
`source` 与 `min_rows: 100`。**绝不锚**当期/最近月份；`scraped_at`/`timestamp` 进
`whitelist_fields`。
