# fao-empres —— FAO EMPRES-i 全球动物疫病事件（WOAH 镜像）

新源接入档案（`templates/new-source/`）。工单：`reports/health-tickets/20261006-fao-empres-2fd55009.yaml`
（`kind=generate`，brief 侦察簿 W3）。

## 数据表面

FAO 通用 **BigQuery SQL 代理模式**：代理只收 `sql_url`（指向 FAO 数据目录里公开的 `.sql` 正文），
免 key、返回 CSV。

| 用途 | 端点 | 说明 |
|---|---|---|
| 取数 | `GET https://api.data.apps.fao.org/api/v2/bigquery?sql_url=<公开 .sql>&<参数>` | 2026-10-07 实测 HTTP 200 `text/csv` |
| SQL 正文 | `https://data.apps.fao.org/catalog/dataset/3ff164cd-8b44-46d3-8f88-92b0361c7878/resource/137a69a0-ad5f-48c3-b927-3a61d2c9a2ce/download/animal-major-disease-parameterized-query.sql` | 目录数据集 `major-diseases-by-date-empres-i`（Animal diseases events - EMPRES-i）现行资源 |

**路径变更留痕**：工单 brief 的 `cat-sql/empres-i/public/latest_outbreaks.sql` 已 404（HTTP 400
`The content retrieved from URL does not appear to be SQL`）；brief 明示「路径以 FAO SQL 目录为准」，
已改用目录现行资源（上表），入口逻辑不变。

入口：`run_fao_empres(limit=100, start_date=None, end_date=None) -> list[dict]`（`spider.py`）。
仅用标准库 `urllib`/`csv`，无新依赖。

## 参数白名单（只发这些，缺一代理即 400）

| 参数 | 值 | 说明 |
|---|---|---|
| `sql_url` | 上表 SQL 正文 URL | 代理入口 |
| `start_date` / `end_date` | `YYYY-MM-DD` | 事件窗口（SQL 内 `COALESCE(observation_date, report_date)` 落窗） |
| `diagnosis_status` / `animal_type` / `disease` / `country` | `all` | 通配取全量；SQL 支持定向值，本单元不发 |

## 字段口径

现行公开视图 17 列（`global_id…country`），**没有 serotype 列**（工单 expectations 里的
`serotype` 不产出、不造数——偏差已回执工单）。行模型按 brief「固定病种固定年计数」为聚合计数：

| 列 | 来源 | 口径 |
|---|---|---|
| `date` | `observation_date`（缺失回落 `report_date`） | 事件月 `YYYY-MM`，与 SQL WHERE 同口径 |
| `country` | `country` | WOAH 通报国名（如 `Poland`） |
| `disease` | `disease` | 主要病种（`African swine fever` / `Influenza - Avian` 等 30 种白名单） |
| `outbreaks` | 计数 | 该（月, 国, 病种）格内事件条数 |
| `url` / `source` / `scraped_at` | 常量/本地 | 追溯与标识 |

`limit` = 聚合行数上限，按 `date` 升序 + country/disease 字典序确定性排序后截取。

## 坑位与容错（brief.notes 逐条落实）

- **免 key 直连**：代理无需鉴权；仅带常规浏览器 UA，不做指纹伪装、不做频次对抗。
- **历史深度**：2023 起可查（实测 2023 全年 1.59 万事件行、2025 全年 2.4 万事件行，
  与 brief「2025 起 4.2 万行」同量级口径）。
- **缺参即 400**：代理会点名缺哪个参数（先报 `country`），6 参必须给全。
- **失败即红**：单一数据表面，任何 HTTP/解析失败或 0 行 → 抛 `RuntimeError`，不静默回空。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 实测样例行（2026-10-07 冒烟，窗口 2026-09 近 30 天）

```json
{"date": "2023-01", "country": "Austria", "disease": "Influenza - Avian", "outbreaks": 38}
{"date": "2025-01", "country": "Poland", "disease": "African swine fever", "outbreaks": 431}
```

## 自检

```bash
python3 spiders/fao-empres/spider.py          # 单页冒烟，打印 3 行聚合计数
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-fao-empres-2fd55009.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-fao-empres.json` 用**固定历史窗口** `2023-01-01..2023-01-31` + `limit=10` 重放，
锚 `2023-01 / Austria / Influenza - Avian / outbreaks=38`（3 年半前的深历史，回填已收敛）。
**绝不锚** 当期窗口计数与抓取时间；`scraped_at`/`timestamp` 进 `whitelist_fields`。
