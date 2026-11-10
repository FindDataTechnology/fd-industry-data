# nasa-firms —— NASA FIRMS 全球 24h 活跃火点（VIIRS C2）

新源接入档案：工单 `reports/health-tickets/20261006-nasa-firms-1bd9e18a.yaml`（`kind=generate`，
brief 侦察簿 W1-I）。修复档案：工单 `reports/health-tickets/20261107-nasa-firms-bd40ec7f.yaml`
（`kind=repair` / `category=fallback`，兜底整段重生成：产品退役换表面）。

## 数据表面

| 优先级 | 端点 | 说明 |
|---|---|---|
| 首选 | `GET https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_24h.csv` | NOAA-20 VIIRS C2 全球 24h 静态 CSV，免 key（MAP_KEY 仅 FIRMS API 需要）；2026-11-09 实测约 9.7 MB / 116,206 行 / 13 列，覆盖最近两个日窗口 |
| 备选 | `GET https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_Global_24h.csv` | NOAA-21 VIIRS C2 全球 24h，同产品族 13 列同构；同日实测约 12.5 MB / 125,284 行 |
| ~~退役~~ | ~~`.../suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Global_24h.csv`~~ | **2026-11-03 起停止供数**：24h/48h 仅剩表头（0 行）、7d 末行停在 2026-11-02；FIRMS 首页 NRT 分发只列 NOAA-20/21 |

降级策略：`spider.py` 的 `SURFACES` 按序取首个「表头合规且至少 1 行」的快照；单次运行
**最多 2 个 GET**，不重试、不加频次、不做频次对抗。全部表面不可用 → 抛 `RuntimeError`
（失败即红，转人工）。

入口：`run_nasa_firms(limit=100) -> list[dict]`（`spider.py`）。仅用标准库
`urllib` + `csv`，无新依赖。

## 字段口径（实测 13 列；2026-10-07 首测，2026-11-09 换表面后复测同名同序）

| 列 | 来源 | 口径 |
|---|---|---|
| `latitude` / `longitude` | 同名列 | 火点坐标（十进制度），完整性约束（缺失行跳过） |
| `bright_ti4` / `bright_ti5` | 同名列 | I4/I5 亮温（K）。工单 expectations 写的 `brightness` 是 MODIS 命名，VIIRS C2 实际为 `bright_ti4/5`，按实相透出不改名 |
| `scan` / `track` | 同名列 | 像元扫描/行进方向尺寸 |
| `frp` | 同名列 | 火辐射功率（MW） |
| `acq_date` | 同名列 | 过境日期 `yyyy-mm-dd`（**每日滚动**） |
| `acq_time` | 同名列 | 过境时刻 HHMM **保留字符串**（不丢前导零，如 `0100`） |
| `satellite` | 同名列 | 上游原值透出：`N20` = NOAA-20（首选表面）、`N21` = NOAA-21（备选）；**随数据表面而变，故不入 golden 锚** |
| `instrument` | 派生常量 | 上游 CSV **无此列**；按产品身份 `*_VIIRS_C2` 补 `VIIRS`（换表面后仍成立，是唯一产品族常量） |
| `confidence` | 同名列 | l / n / h |
| `version` | 同名列 | 处理版本（如 `2.0NRT`） |
| `daynight` | 同名列 | D / N |
| `url` / `source` / `scraped_at` | 实际请求 URL / 常量 / 本地 | 行级可追溯（随降级变）/ `nasa-firms` / UTC ISO8601 |

## 坑位与容错

- **每日滚动**：快照每天全量换血——golden 只锚经常量列（`instrument`/`source`）+ `min_rows`，
  **绝不锚**日期/计数/坐标/当期数值；`satellite`/`url` 随数据表面而变，同样不锚
  （表面优先级由 `spider.py` 的 `SURFACES` 常量 + `manifest.source_url` 守护，代码 review 即门）。
- **产品退役是上游事实，不是解析缺陷**：2026-11 换数据表面时**没有**做协议破解、
  没有加重试/频次，只换同产品族同构 CSV（详见 `spider.py` 模块 docstring 的实测记录）。
- **`instrument` 列上游缺失**：由产品 URL 身份派生常量，README/manifest 双处注明。
- **表头校验**：实测 13 列缺一即判该表面布局异常并降级下一表面（不硬解）；全表面失败 → `RuntimeError`。
- **单行脏数据跳过留痕**（stderr 计数）；快照拉取失败 / 零可用行 → `RuntimeError`
  （失败即红，不静默返回空列表）。
- 只带常规浏览器 UA；不做指纹伪装、不做频次对抗、无签名/验证码处理。
- **`schedule` 保持原值不动**（`7 6 * * *` 为批次二人工点亮结果，spider-heal 不点亮、不改闸）。

## 自检

```bash
python3 spiders/nasa-firms/spider.py          # 冒烟，打印 2 行火点
python3 scripts/health_verify.py --ticket reports/health-tickets/20261107-nasa-firms-bd40ec7f.yaml
python3 scripts/validate_manifests.py && python3 scripts/check_manifest_commands.py
python3 scripts/conformance_gate.py --roots .
```

## golden 断言口径（跨期稳定）

`golden/001-nasa-firms.json`：`params={"limit":5}`（快照恒有 10 万+ 行，min_rows 5 极保守）；
锚 `instrument == VIIRS`、`source == nasa-firms`——即「产品族传感器常量 + 源标识」口径。
**绝不锚** `acq_date`/坐标/亮温/FRP/行数规模等滚动值，也**不锚** `satellite`/`url`（随数据表面变化）：
`scraped_at`/`timestamp` 进 `whitelist_fields`。
