# hf-datasets —— HuggingFace Hub 每月新增数据集计数

新源接入档案（批次二 Wave C 直建；**无工单**，入口行数经手工冒烟验证，见 PR body）。

> **⚠️ 需代理出口**：huggingface.co 主站 API 从中国大陆网络直连不可达。本单元依赖
> 运行环境的 `HTTPS_PROXY`/`HTTP_PROXY` 环境变量（标准库 urllib 默认行为，代码
> **不硬编码代理地址**）。兜底主机 `hf-mirror.com` 可直连（同构 Hub API 反代），主机序
> `huggingface.co → hf-mirror.com`，一个主机整轮翻页失败才换下一个。
> **manifest 刻意不写 `schedule`、不写 `site`：静默合入，代理注入就绪前不点亮。**

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| listing 采样 | `GET https://huggingface.co/api/datasets?limit=100&sort=createdAt&direction=-1` | **免 key、免鉴权**；最新创建优先；翻页跟 `Link: <...>; rel="next"`（opaque cursor） |
| 兜底 | `GET https://hf-mirror.com/api/datasets?...`（同参数） | 直连可达的同构反代；仅在主站整轮失败时使用 |

入口：`run_hf_datasets(limit=100, pages=3) -> list[dict]`（`spider.py`）。
仅用标准库 `urllib` + `json` + `re`，无新依赖。

## 数据集/信号选择理由（口径授权：批次二 Wave C）

- Hub **没有**原生的按月聚合端点（`/api/datasets` 是逐条 listing；无 `countTotal`、
  无日期直方图）。可选周期信号里，「**按月新增数据集数**」最稳：
  - `createdAt` 逐条携带且**不可变**（创建后不改），分桶口径无重述风险；
  - listing 免费、免 key、无配额墙，`sort=createdAt&direction=-1` 排序键稳定；
  - 月度粒度，符合「周期性信号优先」的批次要求。
- **口径为样本口径**：取最新 `pages` 页（默认 3×100≈300 条）分桶到 `YYYY-MM`，
  计数 =「样本中该月新增数」。全站口径需要深翻页（数十万条），不做。行级带
  `sampled_total`（样本量）与 `malformed_skipped`（坏时间戳丢弃数）供对账；
  解读时按 `new_datasets / sampled_total` 的占比趋势看，不读绝对全站量。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `period` | `createdAt` 前缀 | `YYYY-MM`（UTC；`createdAt` 形如 `2026-10-07T12:28:07.000Z`） |
| `new_datasets` | 分桶计数 | 该月新增公开数据集数（**样本口径**，非全站） |
| `sampled_total` | 本轮采样 | 采样条目总数（行级常量，对账用） |
| `malformed_skipped` | 本轮采样 | 缺/坏 `createdAt` 被跳过的条数（行级常量） |
| `endpoint_host` | 实际服务主机 | `huggingface.co` 或兜底 `hf-mirror.com` |
| `url` | 请求 URL | 首页 URL，行级来源可追溯 |
| `source` | 常量 | `hf-datasets` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 坑位与容错

- **翻页 cursor 不透明**：必须逐页解析 `Link` 头 `rel="next"`，不能自拼 offset/页码。
- **主机切换是 all-or-nothing**：单主机任一页失败即弃用该主机整轮样本，从兜底主机
  重取，避免半截样本悄悄通过；**全部主机失败抛 `RuntimeError`**（失败即红）。
- **风控/错主机返回非 JSON**（HTML 挑战页）：统一按解析失败计主机失败。
- **全部条目无可解析 `createdAt`**：抛 `RuntimeError`（不静默返回空列表）。
- 个别条目缺/坏时间戳：跳过并计入 `malformed_skipped`（留痕不致命）。
- `limit` 语义 = 行数上限：期次升序后保留**最近** `limit` 个月；`pages` 钳制在 1..10。
- 免 key、免 UA 伪装；不做指纹伪装与频次对抗，遇风控升级按协议转人工，不逆向。

## golden 锚定策略（计数类）

按批次标准「计数类绝不锚当期总数」：

- **不锚**任何月份的 `new_datasets` 数值——样本窗口随时间滚动（重放日的「最新 100 条」
  覆盖的月份与建样日不同），当期计数必然漂移；
- **不锚** `endpoint_host` / `url`——重放环境可能走 hf-mirror 兜底，主机与 URL 随之切换；
- **只锚结构常量**：`source == "hf-datasets"` + `min_rows: 1`（`golden/001-hf-datasets.json`，
  `params: {limit: 12, pages: 1}` 单页小采样）；
- 跨期稳定不变的 `createdAt` 已建样本（字段级），但计数聚合层面无恒定历史期，故
  不做 `sequence` 参考值比对。

## 历史深度与节奏

- 样本覆盖深度：默认 3 页 ≈ 最新 300 条；Hub 日均新增公开数据集数百量级
  （2026-10-07 实测：最新 3 条 createdAt 落在 3 分钟内，当日新增已数百），
  3 页样本通常覆盖最近 1–2 个月；`pages=10`（上限）≈ 1000 条可回看更久。
- 节奏：月度（Hub listing 实时更新；建议每月初跑上月窗口）。

## 侦察与复测证据（2026-10-07）

- `huggingface.co/api/datasets?limit=3&sort=createdAt&direction=-1`（经代理
  `HTTPS_PROXY=…:7890`）：HTTP 200，返回最新 3 个数据集，`createdAt` 均为当日。
- `hf-mirror.com/api/datasets?limit=1`（直连）：HTTP 200。
- 同代理出口（38.76.150.185）对 socrata/figshare 系被边缘 403（见批次报告），
  HF 主站经同一出口正常——出口可达性按主机逐个确认，不外推。

## 验证记录

- 冒烟：`HTTPS_PROXY/HTTP_PROXY` 置位后 `python3 spiders/hf-datasets/spider.py`
  返回月度行 JSON（行数见 PR body 证据表）。
- 仓级自检：`validate_manifests.py` / `check_manifest_commands.py` /
  `conformance_gate.py --roots .` 全绿（无工单，`health_verify.py` 工单链路不适用）。
