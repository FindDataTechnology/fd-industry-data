# ted-notices —— TED 欧盟招标公告（Tenders Electronic Daily）

新源接入档案（`templates/new-source/`）。工单：
`reports/health-tickets/20261006-ted-notices-0438b7ca.yaml`（`kind=generate`，
brief 侦察簿 W1-M）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 公告检索 | `POST https://api.ted.europa.eu/v3/notices/search`，body `{"query":"publication-date = YYYYMMDD","fields":[白名单],"limit":N,"scope":"ALL"}` | 公开 REST，免 key；`notices[]` 明细 + `totalNoticeCount` 当日总量 |

入口：`run_ted_notices(limit=20, date=None) -> list[dict]`（`spider.py`，`date`
默认 UTC 昨天）。仅用标准库 `urllib` POST，无新依赖。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `date` | `publication-date` | 公告发布日 `YYYY-MM-DD`（**日度序列轴**） |
| `notice_id` | `publication-number` | TED 规范公告号（如 `24031-2024`） |
| `notice_type` | `notice-type` | 公告类型码（`can-standard`/`pin-rtl`…） |
| `buyer` | `organisation-name-buyer` → `tendering-party-name` → `winner-touchpoint-name` | 买方/合同方名；上游投影为空（多数非 eForms 历史公告）如实 `None` |
| `cpv` | `classification-cpv` | CPV 行业码，`;` 连接 |
| `value` / `value_currency` | `tender-value` / `tender-value-highest` / `BT-1118-NoticeResult-Currency` | 合同金额与币种；上游投影为空如实 `None` |
| `title` | `notice-title` | 多语 dict 取 `eng` 优先 |
| `total_notices` | `totalNoticeCount` | 当日公告总量（**日度序列直接可用，单行即含**） |
| `url` | 常量（POST 端点） | 行级来源可追溯 |
| `source` | 常量 | `ted-notices` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 坑位与容错（brief.notes / 实测踩坑逐条落实）

- **只发实测参数**：body 仅 `query`/`fields`/`limit`/`scope` 四键实测有效。
  `fields` 省略或为空 → 400 "must not be empty"；未在白名单内的字段名 → 整个请求
  400（上游会回显 1833 个合法字段名）。
- **日期字面量**：专家检索 `publication-date` 只认 `YYYYMMDD`（上游 pattern 实测）；
  `=` 实测有效（`>= AND <=` 组合实测返回空，不用）。
- **scope 必须 ALL**：枚举实测 `ALL/ACTIVE/LATEST`；历史日期在 `ACTIVE` 下为空
  （公告已归档）。
- **匿名配额极紧**：实测间歇 429（nginx）。单次运行只发 **1 个请求**，串行不并发；
  429 时退避重试（30s → 60s，最多 3 次尝试），不加密频率、不逆向。
- **buyer/value 投影缺口**：字段名被接受但多数历史（非 eForms）公告投影为空——
  行字段如实 `None`，绝不编造；`notice_id`/`date`/`cpv`/`title`/`total_notices`
  实测稳定回填。
- **失败即红**：该日取数彻底失败（含配额退避耗尽）抛 `RuntimeError`；上游合法
  返回 0 条为真实空（返回空列表，不算失败）。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 自检

```bash
python3 spiders/ted-notices/spider.py          # 冒烟（默认昨日，1 请求）
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-ted-notices-0438b7ca.yaml
```

注意：验证链会消耗匿名配额（golden 重放 1 请求 + real_fetch 1 请求）；若 429，
spider 内部退避重试，仍红则等配额窗口恢复再跑。

## golden 断言口径（跨期稳定）

`golden/001-ted-notices.json` 锚**固定历史日期段**（brief 指定策略）：
`params = {limit: 10, date: "2024-01-15"}`，`rows_contains` 锚
`date == 2024-01-15`（参数钉死）、`source == ted-notices`（常量）、
`notice_id == 24031-2024`（该日实测首条公告 id，固定历史值），
以及 `min_rows: 1`。
**绝不锚** `total_notices` 计数与当期数值；`scraped_at`/`timestamp` 进
`whitelist_fields`。

## 实测样例（2026-10-06/07 冒烟与探针）

`publication-date = 20240115`：`totalNoticeCount = 3530`，首条
`24031-2024`（can-standard，法国工程服务公告），`classification-cpv` 稳定回填
（如 `["71300000"]`），`notice-title.eng` 稳定回填；buyer/value 在该批公告投影为空
（非 eForms 时代公告），如实置 `None`。
