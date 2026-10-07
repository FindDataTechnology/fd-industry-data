# ember —— Ember 国别月度电力结构（发电量 + 占比）

新源接入档案（`templates/new-source/`）。工单：
`reports/health-tickets/20261007-ember-e945ff89.yaml`（`kind=generate`，批次二 Wave B keyed 源直建）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 月度发电结构 | `GET https://api.ember-energy.org/v1/electricity-generation/monthly` | key 鉴权；JSON；实体枚举 215 国/地区 |
| 机读 spec | `GET https://api.ember-energy.org/v1/openapi.json` | 参数与 dataset 枚举（不进主流程，仅排障） |

入口：`run_ember(limit=100, entity_code="DEU", start_date=None, end_date=None, series=None) -> list[dict]`
（`spider.py`）。仅标准库 `urllib`/`json`/`re`/`datetime`，无新依赖。

### 参数白名单（仅发实测过的）

| 参数 | 取值 | 备注 |
|---|---|---|
| `api_key` | env `EMBER_API_KEY` | **查询参数形态**（非请求头；spec 代码示例实锤）；缺 key → 403 `{"detail":"No API key set"}`，本单元先抛错不裸跑 |
| `entity_code` | **ISO3**（`DEU`） | `DE` 等 ISO2 **静默返回 0 行**（最易踩的坑）；本单元校验码形且空结果即红 |
| `start_date` / `end_date` | `YYYY-MM` | 必须成对给出；缺省 = 最近 6 个月（至当月） |
| `series` | 精确序列名（可选） | 实测有效（`series=Wind` → 1 行）；未知名 → 0 行 → 本单元抛错并列出实测枚举 |

### 实测 ``series`` 枚举（DEU，17 项）

`Bioenergy`、`Clean`*、`Coal`、`Demand`*、`Fossil`*、`Gas`、`Hydro`、
`Hydro, bioenergy and other renewables`*、`Net imports`、`Nuclear`、`Other fossil`、
`Other renewables`、`Renewables`*、`Solar`、`Total generation`*、`Wind`、`Wind and solar`*
（`*` = 上游标记 `is_aggregate_series: true` 的聚合项，随行输出为布尔列）

## 字段口径

**上游为宽表**：一行同时载两个指标（`generation_twh` + `share_of_generation_pct`），
单位内嵌在列名里，**上游不返回 unit 列**——故本单元不臆造 `unit` 列，单位在 manifest 的
`description` / `concepts.unit` 中声明。

| 列 | 来源 | 口径 |
|---|---|---|
| `month` | `data[].date` | **月度粒度**：上游恒为当月 1 日（`2024-01-01`）→ 归一 `2024-01` |
| `entity` / `entity_code` | `data[].entity` / `.entity_code` | 显示名（Germany）/ ISO3（DEU） |
| `series` | `data[].series` | 序列名（Coal/Gas/Wind/…，见上枚举） |
| `is_aggregate_series` / `is_aggregate_entity` | 同名上游字段 | 布尔（聚合序列 / 聚合实体，如 EU27） |
| `generation_twh` | `data[].generation_twh` | 发电量 TWh（`Net imports` 为负值） |
| `share_of_generation_pct` | `data[].share_of_generation_pct` | 占总发电量百分比（`Demand` 类可 >100） |
| `url` | 请求 URL | **已脱敏**：`api_key=***`（密钥不落行/落盘/落日志） |
| `source` | 常量 | `ember` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 坑位与容错（brief.notes 逐条落实，均 2026-10-07 实测）

- **key 纪律（最高优先级）**：`os.environ["EMBER_API_KEY"]` 读取；缺失抛 `RuntimeError`
  （指名 env 变量与来源），**不硬编码、不出现在任何文件/日志/行数据**。行内 `url` 与所有异常
  文本都用脱敏 URL（`api_key=***`）——可用 `grep -r "api_key=[^*\"]" spiders/ember` 自证。
- **ISO3 而非 ISO2**：`entity_code=DE` → HTTP 200 + `number_of_records: 0`（**静默判空，不报错**）。
  本单元对空结果抛 `RuntimeError` 并在消息里点名该坑位——把"静默空"变成"红"。
- **重复 `entity_code` 不聚合**：实测 `entity_code=DEU&entity_code=FRA` 只按**最后一个**取值
  （`stats.query_parameters_used` 仅记 FRA，返回 16 行 FRA 数据）→ 本单元只接受单个实体，
  需要多国请多次调用或外部合并（不猜测上游语义）。
- **上游 `limit` 参数对行数无效**：实测 `limit=3` 仍回 17 行（`stats` 原样回显 `limit:["3"]`）。
  本单元的 `limit` 是**客户端尾部截取**（`(month, series)` 升序后取最新 `limit` 行），
  不做上游分页；可用行数看 `stats.number_of_records`（不透出为列，避免制造非数据列）。
- **月度发布滞后**：实测 2026-10-07 时当前最新可用月为 **2026-09**（DEU）→ 缺省窗口取 6 个月
  留裕量；`date` 恒为当月 1 日，故 `month` 归一不丢信息。
- **历史深度**：DEU 月度序列自 `2015-01` 起（`2014-01` 及更早 `number_of_records: 0`，实测）。
- **限速**：官方未公示数值限速（响应 `stats.rate_limit: "No"`）；月度任务量级 + 单次 1 请求，
  不并发、不轮询、不做频次对抗。
- **首连偶发失败**：仅重试 1 次（共 2 次尝试，2s 退避）；4xx（除 408/429）不重试。
- **失败即红**：0 行 / 负载缺 `data` → `RuntimeError`，不静默返回空列表。
- **无 schedule / 无 site**：manifest 刻意不写，静默合入后由人工点亮。

## golden 断言口径（跨期稳定）

`golden/001-ember.json` 用**固定历史月** `entity_code=DEU`、`2024-01`、`limit=17`：

- `min_rows: 17`（该月 17 个序列的结构常量）；
- 锚冻结值：`Wind generation_twh=18.33`、`Wind share=38.42`、`Coal generation_twh=10.38`、
  `Total generation generation_twh=47.7`（构建时两次实况读取一致）；
- 锚结构常量：`month=2024-01`、`entity=Germany`、`entity_code=DEU`、序列名、`source=ember`。

**绝不当期锚**（当前最新月随月度更新滚动）；`scraped_at`/`timestamp` 进 `whitelist_fields`。
`limit=17` + 锚中后段序列（Wind/Coal/Total generation）——即便上游新增序列导致尾部截取少一行，
锚点依然命中。

## 自检

```bash
source /Users/chengsishi/finddata/secrets/fd-industry-source-keys.env   # 本地注入 key
python3 spiders/ember/spider.py                                        # 真实冒烟（2024-01 DEU 3 行）
python3 scripts/check_manifest_commands.py
python3 scripts/health_verify.py --ticket reports/health-tickets/20261007-ember-e945ff89.yaml
```

## 实测证据（2026-10-07）

| 项 | 结果 |
|---|---|
| 冻结窗口稳定性 | DEU `2024-01` 两次读取 `data[]` 一致（`stats.timestamp`/`response_time` 逐次变动，故不入行） |
| 行数 | DEU `2024-01` = 17 行；`2024-01..2024-03` = **51 行**（brief 的 51 即 3 个月 × 17） |
| 无 `entity_code` 过滤 | 省略该参数会返回全部实体（215 国 × 月份），故本单元**强制**单实体 + 窗口 |
| ISO2 坑位 | `entity_code=DE` → 200 + 0 记录（静默） |
| 缺 key | HTTP 403 `{"detail":"No API key set"}` |
| 历史深度 | 2015-01 有值；2014-01/2010-01/2000-01 均 0 记录 |
| 当期滞后 | 2026-10-07 时最新月 = 2026-09（滞后 ≈1 月） |

> 注：brief.expectations 里的行字段（`area/date/variable/category/unit/value`）是 Ember **旧版**
> 形态；当前 v1 API 实测为上述宽表（`entity/entity_code/date/series/generation_twh/
> share_of_generation_pct`），本单元按**实测**形状建模，偏差已记录在 PR 说明与本节。