# eia-v2 —— EIA 美国小时级电网运行数据（RTO region-data）

新源接入档案（`templates/new-source/`）。工单：
`reports/health-tickets/20261007-eia-v2-b55acdfb.yaml`（`kind=generate`，批次二 Wave B keyed 源直建）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 小时数据 | `GET https://api.eia.gov/v2/electricity/rto/region-data/data/` | key 鉴权；JSON |
| 口径基准 | 同端点 `response.data[].type` | `D` 实际需求 / `DF` 日前需求预测 / `NG` 净发电 / `TI` 总联络线交换 |

入口：`run_eia_v2(limit=100, respondent="CISO", start=None, end=None, series_type=None) -> list[dict]`
（`spider.py`）。仅标准库 `urllib`/`json`/`re`/`datetime`，无新依赖。

### 参数白名单（仅发实测过的）

| 参数 | 取值 | 备注 |
|---|---|---|
| `api_key` | env `EIA_API_KEY` | 查询参数形态（非请求头）；缺 key 立即抛错，禁止裸跑 |
| `frequency` | `hourly` | 本单元固定；数据自 `2019-01-01T00` 起（CISO 实测） |
| `data[0]` | `value` | 只取数值列 |
| `facets[respondent][]` | BA 码（`CISO`/`PJM`/`ERCO`…） | **不是州名**；形状校验 `^[A-Z0-9-]{2,12}$` |
| `facets[type][]` | `D`/`DF`/`NG`/`TI`（可选） | 不给 = 全四类 |
| `start` / `end` | `YYYY-MM-DDTHH`（UTC） | 必须成对给出；缺省 = 最近若干小时（按 `limit` 反推 + 48h 滞后裕量，上限 30 天） |
| `sort[0][column]` / `sort[0][direction]` | `period` / `asc` | 固定升序，尾部截取语义确定 |
| `offset` / `length` | `length ≤ 5000` | 分页取窗口尾部（`offset = total - limit` 直达），不全量拉取 |

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `period` | `data[].period` | 小时（UTC，EIA `dateFormat=YYYY-MM-DD"T"HH24`），**原样字符串** |
| `respondent` / `respondent_name` | `data[].respondent` / `.respondent-name` | BA 码 / 显示名 |
| `series_type` / `series_type_name` | `data[].type` / `.type-name` | 序列码（D/DF/NG/TI）/ 序列名 |
| `value` | `data[].value` | **上游为字符串**（`"22497"`），本单元转 `float`；不可解析 → `None` |
| `unit` | `data[].value-units` | 实测 `megawatthours` |
| `url` | 请求 URL | **已脱敏**：`api_key=***`（密钥不落行/落盘/落日志） |
| `source` | 常量 | `eia-v2` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 坑位与容错（brief.notes 逐条落实，均 2026-10-07 实测）

- **key 纪律（最高优先级）**：`os.environ["EIA_API_KEY"]` 读取；缺失抛
  `RuntimeError`（指名 env 变量与来源），**不硬编码、不出现在任何文件/日志/行数据**。
  行内 `url` 与所有异常文本都用脱敏 URL（`api_key=***`）——可用 `grep -r "api_key=[^*\"]"` 自证。
- **限流**：<9000 次/时、<5 次/秒。单次运行 1–2 个请求（分页按 0.3s 间隔），无并发、无轮询，
  远低于限额；不做任何频次对抗。
- **5000 行/请求上限**：`length > 5000` 会被上游**忽略长度**并回
  `warnings: ["parameter out of range: length"]`（实测 `length=6000` 仍返回整窗口），
  故本单元硬顶 5000，超量走 `offset` 分页且只取尾部。
- **截断信号**：`warnings[].warning == "incomplete return"` 在 `返回行数 < total` 时出现
  （实测 `total=16`、`length=5` 也会出现），**不能**据此判断"是否撞 5000 上限"；可靠判据是
  `len(data) < int(response.total)`（本单元只用后者）。`response.total` 亦是**字符串**。
- **鉴权错误**：403 + `{"error":{"code":"API_KEY_MISSING"}}` / `API_KEY_INVALID` → 立即抛
  `RuntimeError` 并指名解决办法，不重试（避免放大）。
- **首连偶发失败**：仅重试 1 次（共 2 次尝试，2s 退避）；4xx（除 408/429）不重试。
- **四序列播报滞后不同**（2026-10-07T11 UTC 实测 CISO 最末行）：`D` 近实时（`T10`）、
  `DF` ≈ -4h（`T07`）、`NG` ≈ -5h（`T06`）、`TI` ≈ -28h（`2026-10-06T07`）。故窗口**尾部小时
  只含部分序列**（实测最近 35 小时中 27 小时不足 4 序列）——属上游行为，原样透出，
  不在客户端补齐或丢弃；下游按 `series_type` 自行对齐。
- **历史深度**：hourly `region-data` 自 `2019-01-01T00` 起（`2018-12-31` 窗口返回空，实测）。
- **失败即红**：窗口内 0 行 → `RuntimeError`（提示 BA 码/窗口与历史深度），不静默返回空列表。
- **无 schedule / 无 site**：manifest 刻意不写，静默合入后由人工点亮。

## golden 断言口径（跨期稳定）

`golden/001-eia-v2.json` 用**固定历史窗口** `respondent=CISO`、`2020-01-01T00..03`、`limit=16`：

- `min_rows: 16`（4 小时 × 4 序列的结构常量）；
- 锚冻结值 `value ∈ {22497.0(D), 22319.0(DF), 11294.0(NG), -5797.0(TI)}`（该小时四序列原值，
  构建时两次实况读取逐字节一致）；
- 锚结构常量 `period`/`respondent`/`respondent_name`/四个 `series_type`/`series_type_name`/`unit`/`source`。

**绝不当期锚**（最新小时仅部分序列、且逐时变动）；`scraped_at`/`timestamp` 进 `whitelist_fields`。

## 自检

```bash
source /Users/chengsishi/finddata/secrets/fd-industry-source-keys.env   # 本地注入 key
python3 spiders/eia-v2/spider.py                                       # 真实冒烟（当期 4 行）
python3 scripts/check_manifest_commands.py
python3 scripts/health_verify.py --ticket reports/health-tickets/20261007-eia-v2-b55acdfb.yaml
```

## 实测证据（2026-10-07）

| 项 | 结果 |
|---|---|
| 冻结窗口稳定性 | `2020-01-01T00..03` 两次读取**逐字节一致**（total=16） |
| 分页 | `length=5000` 首页 + `offset=5000` 第二页衔接正常（`2020-02-22T02` 起） |
| 长度越界 | `length=6000` → 告警 `parameter out of range: length`，length 被忽略 |
| 缺 key | HTTP 403 `API_KEY_MISSING`；错 key → HTTP 403 `API_KEY_INVALID` |
| 历史深度 | 2019-01 有值；2018-12 空 |
| 当期延迟 | 各序列不同：`D` 近实时、`DF` ≈-4h、`NG` ≈-5h、`TI` ≈-28h（实测 T11 UTC）；保留 48h 窗口裕量 |