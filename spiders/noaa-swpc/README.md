# noaa-swpc —— NOAA 行星 Kp 指数日级统计

新源接入档案（批次三直建；**无工单**，入口行数经手工冒烟验证，见 PR body）。

> **直连可达、免 key**：`services.swpc.noaa.gov` 公开 products 端点，2026-10-08
> 直连实测 HTTP 200（无需代理出口）。manifest **不写 `schedule`、不写 `site`、
> 无 `egress_secret`**——静默合入，点亮排期由人工门统一补。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 唯一端点 | `GET https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json` | **免 key、免鉴权**；返回滚动窗（约 8–30 天）内逐 3 小时一条的行星 Kp 观测 |

入口：`run_noaa_swpc(limit=100) -> list[dict]`（`spider.py`）。
仅用标准库 `urllib` + `json`，无新依赖。

## 信号选择理由（口径授权：批次三直建）

- SWPC products 家族里 Kp 是**最稳的周期性空间天气信号**：行星 Kp 是全球地磁活动
  的标准指数（0–9，1/3 步长），3h 观测连续滚动，无配额墙；
- 3h 粒度对日频消费太碎，按 **UTC 日期分桶**出日级统计：`daily_max_kp`
  （当日最大 Kp，空间天气日报的标准口径，G1–G5 地磁暴分级即按日最大 Kp 划分）、
  `daily_avg_kp`（当日均值）、`obs_count`（当日 3h 观测条数，对账用）；
- 相较备选的 3h 原始行（行数多、粒度碎）与预报类产品（`noaa-planetary-k-index-forecast.json`
  数值会随模式更新重述），**观测类产品的历史值不可变**，分桶口径无重述风险。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `period` | `time_tag` 前缀 | `YYYY-MM-DD`（UTC 日期桶；NOAA `time_tag` 本身即 UTC） |
| `daily_max_kp` | 分桶聚合 | 当日最大 Kp（0–9 准对数标度） |
| `daily_avg_kp` | 分桶聚合 | 当日 3h 观测的 Kp 算术均值 |
| `obs_count` | 分桶计数 | 当日有效 3h 观测条数（满日 8 条；**末行可能是半天**） |
| `url` | 请求 URL | 行级来源可追溯 |
| `source` | 常量 | `noaa-swpc` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

辅助列 `a_running`（3h running Ap）与 `station_count`（参与统计台站数）不进
输出行——两者是 3h 粒度属性，日级聚合无标准口径，避免自造口径。

## 坑位与容错

- **响应形态有两种，单元两者都吃**：SWPC products 家族历史上既有「首行字符串表头
  + 数组行」（`["time_tag","Kp","a_running","station_count"]`）也有「对象数组」。
  2026-10-08 实测为对象数组。单元按首元素类型判别后统一成 dict；表头出现未知列
  或形态不识别即抛 `RuntimeError`（schema 漂移要响，不要静默兼容）。
- **首行是表头要跳过**（形态 1 下）：按表头名映射取列，不按位置硬编码。
- **Kp 可能字符串化**：统一 `float()` 转换；坏值跳过该条观测（不丢整日，除非该日
  观测全坏——全空则抛 `RuntimeError`）。
- **滚动窗**：窗口随时间滚动（实测 2026-10-08 窗口约 8 天，官方描述可达 ~30 天），
  重放日的最新日期与建样日不同。
- **末行可能是半天**：当日 8 个 3h 档未跑满时，该日统计基于已有观测——解读当日
  max/avg 前先看 `obs_count`。
- `limit` 语义 = 行数上限：期次升序后保留**最近** `limit` 日；钳制在 1..1000。
- 免 key、免 UA 伪装；不做指纹伪装与频次对抗，遇风控升级按协议转人工，不逆向。

## golden 锚定策略（滚动窗口计数类）

按批次标准「滚动窗/计数类绝不锚当期值」：

- **不锚**任何 `daily_max_kp` / `daily_avg_kp` / `obs_count` 数值——Kp 随地磁
  活动逐 3h 变化，窗口滚动导致重放覆盖的日期与建样日不同，当期值必然漂移；
- **不锚** `period` 具体日期、`url`——同理随窗口滚动；
- **只锚结构常量**：`source == "noaa-swpc"` + `period` 格式（`^\d{4}-\d{2}-\d{2}$`）
  + `obs_count` 非负整数格式 + `min_rows: 1`（`golden/001-noaa-swpc.json`，
  `params: {limit: 12}`）。

## 历史深度与节奏

- 端点只供滚动窗（约 8–30 天），**无历史回补面**；日级统计建议每日跑一次取最新。
- 深历史如需回补，NOAA NCEI 另有 Kp Ap 存档端点（`https://www.ngdc.noaa.gov/geomag-data/...`
  / SPIDR），属另一个源的事，本单元不做。

## 侦察与复测证据（2026-10-08）

- `GET https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json`（直连，
  无代理）：**HTTP 200**，4566 bytes，59 条 3h 观测，窗 2026-10-01 → 2026-10-08
  （约 8 天），形态为对象数组，`Kp` 为数值型（0.33/1.0/…）。
- 同日冒烟 `run_noaa_swpc()`：日级行数与首末行见 PR body 证据表。

## 验证记录

- 冒烟：`python3 spiders/noaa-swpc/spider.py` 直连返回日级统计 JSON（行数与首末行
  见 PR body 证据表）。
- 仓级自检：`validate_manifests.py` / `check_manifest_commands.py` /
  `conformance_gate.py --roots .` 全绿（无工单，`health_verify.py` 工单链路不适用）。
