# nasa-firms —— NASA FIRMS 全球 24h 活跃火点（VIIRS，平台阶梯）

新源接入档案。工单：`reports/health-tickets/20261006-nasa-firms-1bd9e18a.yaml`
（`kind=generate`，brief 侦察簿 W1-I）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 24h 火点快照（首选） | `GET .../noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_24h.csv` | 静态产品 CSV，免 key（MAP_KEY 仅 FIRMS API 需要），每日滚动 |
| 24h 火点快照（备选 1） | `GET .../noaa-21-viirs-c2/csv/J2_VIIRS_C2_Global_24h.csv` | 同上 |
| 24h 火点快照（备选 2） | `GET .../suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Global_24h.csv` | 同上；**2026-11-10 实测该产品已停更**，排最后 |

入口：`run_nasa_firms(limit=100) -> list[dict]`（`spider.py`）。仅用标准库
`urllib` + `csv`，无新依赖。

## 字段口径（实测 13 列，2026-10-07 直连冒烟）

| 列 | 来源 | 口径 |
|---|---|---|
| `latitude` / `longitude` | 同名列 | 火点坐标（十进制度），完整性约束（缺失行跳过） |
| `bright_ti4` / `bright_ti5` | 同名列 | I4/I5 亮温（K）。工单 expectations 写的 `brightness` 是 MODIS 命名，VIIRS C2 实际为 `bright_ti4/5`，按实相透出不改名 |
| `scan` / `track` | 同名列 | 像元扫描/行进方向尺寸 |
| `frp` | 同名列 | 火辐射功率（MW） |
| `acq_date` | 同名列 | 过境日期 `yyyy-mm-dd`（**每日滚动**） |
| `acq_time` | 同名列 | 过境时刻 HHMM **保留字符串**（不丢前导零，如 `0117`） |
| `satellite` | 同名列 | 服务该快照的平台：`N20` = NOAA-20 / `N21` = NOAA-21 / `N` = Suomi NPP |
| `instrument` | 派生常量 | 上游 CSV **无此列**；各平台产品同为 VIIRS C2，补常量 `VIIRS` |
| `confidence` | 同名列 | l / n / h |
| `version` | 同名列 | 处理版本（如 `2.0NRT`） |
| `daynight` | 同名列 | D / N |
| `url` / `source` / `scraped_at` | 请求 URL / 常量 / 本地 | 行级可追溯 / `nasa-firms` / UTC ISO8601 |

## 坑位与容错（brief.notes 逐条落实）

- **每日滚动**：快照每天全量换血——golden 只锚经常量列（`instrument`/`source`，
  即列名集合）+ `min_rows`，**绝不锚**日期/计数/坐标/当期数值。`satellite`/`url`
  随平台阶梯变化，**不再进 golden**（2026-11-10 起）。
- **快照可用性三条判据**（缺一条就会静默灌脏数据）：
  1. 有数据行（只有表头 = 上游把该平台发空）；
  2. 最新 `acq_date` 距今不超过 `MAX_SNAPSHOT_AGE_DAYS = 2`（24h 窗口跨 UTC 日界）；
  3. 逐平台降级，全废才 `RuntimeError`，错误里逐条写清每个平台的原因。
  **实测教训（2026-11-10）**：S-NPP 的 7d 文件有 35,261 行、HTTP 200、文件当天还在
  重新生成，但 `acq_date` **全部是 2026-11-02** —— 停更 8 天。只判「有没有行」会把
  8 天前的火点当今日快照写进库。
- **`instrument` 列上游缺失**：由产品 URL 身份派生常量，README/manifest 双处注明。
- **表头校验**：实测 13 列缺一即抛 `RuntimeError`（上游布局变更转人工，不硬解）。
- **单行脏数据跳过留痕**（stderr 计数）；快照拉取失败 / 零可用行 → `RuntimeError`
  （失败即红，不静默返回空列表）。
- 只带常规浏览器 UA；不做指纹伪装、不做频次对抗、无签名/验证码处理。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 自检

```bash
python3 spiders/nasa-firms/spider.py          # 冒烟，打印 2 行火点
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-nasa-firms-1bd9e18a.yaml
python3 scripts/validate_manifests.py && python3 scripts/check_manifest_commands.py
python3 scripts/conformance_gate.py --roots .
```

## golden 断言口径（跨期稳定）

`golden/001-nasa-firms.json`：`params={"limit":5}`（快照恒有数万行，min_rows 5
极保守）；锚 `instrument == VIIRS`、`source == nasa-firms`——即「列名集合 +
instrument 常量」口径。`satellite`/`url` 已移出 golden：平台阶梯会让它们变化。
**绝不锚** `acq_date`/坐标/亮温/FRP/行数规模等任何滚动值；
`scraped_at`/`timestamp` 进 `whitelist_fields`。
