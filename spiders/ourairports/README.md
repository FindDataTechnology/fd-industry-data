# ourairports —— OurAirports 机场维表类型计数快照

新源接入档案（批次三直建；**无工单**，入口行数经手工冒烟验证，见 PR body）。

> **直连可达、免 key**：`davidmegginson.github.io`（GitHub Pages）公开数据集，
> 2026-10-08 直连实测 HTTP 200（约 12.7 MB，无需代理出口）。manifest **不写
> `schedule`、不写 `site`、无 `egress_secret`**——静默合入，点亮排期由人工门统一补。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 唯一端点 | `GET https://davidmegginson.github.io/ourairports-data/airports.csv` | **免 key、免鉴权**；全量机场维表 CSV，19 列，约 8.6 万行，12.7 MB |

入口：`run_ourairports(limit=100) -> list[dict]`（`spider.py`）。
仅用标准库 `urllib` + `csv` + `io`，无新依赖。

## 信号选择理由（口径授权：批次三直建）

- OurAirports 是**公开可再分发的全球机场维表**（社区维护，上游约每月全量重建），
  逐行明细无周期性——可稳提取的周期信号是「**类型计数快照**」：按 `type` 分桶
  计数 + `total_airports` 总计，月度对比即可看出各类型机场池增减；
- `type` 是维表的**官方一等分类**（`large_airport`/`medium_airport`/
  `small_airport`/`heliport`/`closed`/`seaplane_base`/`balloonport`），口径
  上游定义、无歧义；相较自造的按国家/大洲分桶（组合爆炸、且属切维不是信号），
  类型计数最小、最稳、可跨月对账；
- **快照是当期值**：维表月度重建且上游不保留历史版本，`total` 语义 =
  「本期维表中的该类型机场数」，无重述风险（每期快照自洽）。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `period` | 抓取日 | `YYYY-MM-DD`（UTC 快照日；**维表无观测时间戳**，`period` 语义是「该日维表状态」，不是业务发生日） |
| `airport_type` | `type` 列 | 维表官方类型枚举 |
| `total` | 流式计数 | 本期维表中该类型机场数 |
| `total_airports` | 流式计数 | 有 type 的行总数（**行级常量**，对账 `== sum(type totals)`） |
| `malformed_skipped` | 流式计数 | `type` 为空的行数（行级常量；不进桶、不计入 total_airports，保持可对账） |
| `url` | 请求 URL | 行级来源可追溯 |
| `source` | 常量 | `ourairports` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

其余 16 列（坐标/名称/编码/链接等明细）不进输出行——单值化无意义，明细消费
应直连上游维表按 `ident` 关联，本单元只出计数信号。

## 坑位与容错

- **必须流式解析**：12.7 MB 全量 `resp.read()` 会整块进内存——本单元用
  `io.TextIOWrapper` 包响应流、`csv.DictReader` 逐行读，行读完即弃（防 OOM 纪律）。
- **上游月度重建，计数是当期值**：两期之间各类型计数会漂移（上游还会改type 归类、
  清理重复），golden 绝不锚计数；`closed` 类型专门收纳关闭机场，数量同样随上游
  清理波动。
- **19 列 schema**：2026-10-08 实测表头含 `local_code`（早期档案常漏记）；
  `csv.DictReader` 按表头名取列、对列序/增列免疫，但 **`type` 列消失即抛
  `RuntimeError`**（schema 漂移要响）。
- **编码/坏行**：`errors="replace"` 容忍个别坏字节；CSV 解析中途断流包成
  `RuntimeError`（失败即红）。
- `limit` 语义 = 类型行数上限：按 `total` 降序保留前 `limit` 个类型
  （当前全类型 7 个，默认 100 即全量）；钳制在 1..1000。
- 免 key、免 UA 伪装；不做指纹伪装与频次对抗，遇风控升级按协议转人工，不逆向。

## golden 锚定策略（快照计数类）

按批次标准「计数类绝不锚当期总数」：

- **不锚**任何 `total` / `total_airports` / `malformed_skipped` 数值——维表月度
  重建，当期计数必然漂移；
- **不锚** `airport_type` 具体枚举集合——上游可能新增/合并类型（只锚其格式
  `^[a-z_]+$`）；`period`/`url` 同理不锚具体值；
- **只锚结构常量**：`source == "ourairports"` + `period` 格式
  （`^\d{4}-\d{2}-\d{2}$`）+ `min_rows: 1`（`golden/001-ourairports.json`，
  `params: {limit: 12}`）。

## 历史深度与节奏

- 端点只供**当期快照**（上游约每月重建，不保留历史版本）；本仓每期抓取自然
  形成时间序列；节奏建议月度（对齐上游重建频率）。
- 深历史：OurAirports 站点提供按月归档 zip，本单元不做（快照信号已满足需求）。

## 侦察与复测证据（2026-10-08）

- `GET https://davidmegginson.github.io/ourairports-data/airports.csv`（直连，
  无代理）：**HTTP 200**，12,742,884 bytes（12.7 MB），19 列，86,223 行有效 type。
- 类型分布（当期）：`small_airport` 42,802 / `heliport` 23,238 / `closed` 13,567 /
  `medium_airport` 4,105 / `large_airport` 1,175 / `seaplane_base` 1,273 /
  `balloonport` 63；空 type 行 0。
- 同日冒烟 `run_ourairports()`：行数与首末行见 PR body 证据表。

## 验证记录

- 冒烟：`python3 spiders/ourairports/spider.py` 直连流式解析返回类型计数 JSON
  （行数与首末行见 PR body 证据表）。
- 仓级自检：`validate_manifests.py` / `check_manifest_commands.py` /
  `conformance_gate.py --roots .` 全绿（无工单，`health_verify.py` 工单链路不适用）。
