# twse-monthly-revenue —— 台灣證交所上市公司當月營收快照

新源接入档案（`templates/new-source/`）。工单：`reports/health-tickets/20261006-twse-monthly-revenue-e1427690.yaml`
（`kind=generate`，批次二，侦察簿 W2-1）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 当月营收快照 | `GET https://openapi.twse.com.tw/v1/opendata/t187ap05_L` | 公开 OpenAPI，**免 key 纯 JSON 直连**，返回数组（~1086 行） |

入口：`run_twse_monthly_revenue(limit=100, industry=None) -> list[dict]`（`spider.py`）。
仅用标准库 `urllib`，无新依赖。

## 参数白名单（只发实测过的参数）

无请求参数（端点不接受 query）；单元侧 `limit`（行数上限）与 `industry`（產業別子串过滤，
如 `半導體` 命中 `半導體業`）为本地参数。

## 字段口径

| 列 | 来源（上游中文键） | 口径 |
|---|---|---|
| `code` | `公司代號` | 上市公司代码 |
| `name` | `公司名稱` | 公司简称 |
| `industry` | `產業別` | 产业别（如 `半導體業`，可过滤半导体类） |
| `data_month` | `資料年月` | 民国 `11508` → ISO `2026-08`（+1911） |
| `report_date` | `出表日期` | 民国 `1150917` → ISO `2026-09-17` |
| `revenue_current` | `營業收入-當月營收` | 当月营收，**千元新台币** |
| `revenue_prev` | `營業收入-上月營收` | 上月营收（千元） |
| `revenue_last_year` | `營業收入-去年當月營收` | 去年同月营收（千元） |
| `mom_pct` / `yoy_pct` | `營業收入-上月比較增減(%)` / `去年同月增減(%)` | 环比/同比 % |
| `revenue_accum` | `累計營業收入-當月累計營收` | 年累计营收（千元） |
| `note` | `備註` | 备注（`-` 归一 None） |
| `url` / `source` / `scraped_at` | 请求 URL / 常量 / 本地 | 行级溯源 |

## 历史深度与快照语义

- **无历史回溯**：端点只透出当期快照（`資料年月` = 上一個月）。历史靠调度按
  `monthly` 节奏**正向累积**，每期落一行/公司；本单元不做任何回溯拼接。

## 坑位与容错（brief.notes 逐条落实）

- **民国纪年**：`資料年月`/`出表日期` 均 +1911 归一 ISO；已 ISO 形态原样透出。
- **缺测占位**：`-`/空串（营收、百分比、備註）一律归一 `None`，绝不作 0 透出。
- **失败即红**：仅一页，全量失败/过滤后为空抛 `RuntimeError`；单行结构异常跳过留痕。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 自检

```bash
python3 spiders/twse-monthly-revenue/spider.py          # 冒烟，打印 2 行快照
python3 scripts/check_manifest_commands.py               # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-twse-monthly-revenue-e1427690.yaml
```

## 实测样例行（2026-10-06）

```json
{"code": "1101", "name": "台泥", "industry": "水泥工業", "data_month": "2026-08",
 "report_date": "2026-09-17", "revenue_current": 13515534.0, "revenue_prev": 13744103.0,
 "revenue_last_year": 12214776.0, "mom_pct": -1.663, "yoy_pct": 10.649,
 "revenue_accum": 98726969.0, "note": null, "url": "…/t187ap05_L",
 "source": "twse-monthly-revenue", "scraped_at": "…"}
```

半导体过滤实测：`industry=半導體` → 96 行（2302/2303/2329/2330…）。

## golden 断言口径（跨期稳定，只锚结构）

`golden/001-twse-monthly-revenue.json` 用 `params` 钉 `industry=半導體` + `limit=200`，
锚**结构性常量**：过滤后 `industry == 半導體業` 非空（`min_rows: 5`）、`code == 2330`
（台积电，半導體業最稳定成员）在列、`source`/`url` 常量。
**绝不锚** 当月营收数值、`data_month`/`report_date`（每期变化）与 `scraped_at`
（`scraped_at`/`timestamp` 在 `whitelist_fields`）。
