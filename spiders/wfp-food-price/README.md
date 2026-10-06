# wfp-food-price —— WFP 全球市场粮价月度（HDX 托管）

新源接入档案（`templates/new-source/`）。工单：`reports/health-tickets/20261006-wfp-food-price-a8b9bfd6.yaml`
（`kind=generate`，拷问 Q1 裁决纳入：官方统计口径）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 资源解析 | `GET https://data.humdata.org/api/3/action/package_show?id=wfp-food-prices` | CKAN JSON，`result.resources[]` 取 `format == "CSV"` 的现行下载 URL |
| 资源本体 | 上一步解析出的 `https://data.humdata.org/dataset/4fdcd4dc-…/resource/12d7c8e3-…/download/wfpvam_foodprices.csv` | 全库约 200MB：90+ 国 × 市场级 × 月度主粮价（1990 起；2021-08 后归档停更） |

入口：`run_wfp_food_price(limit=100) -> list[dict]`（`spider.py`）。仅标准库
`urllib/json/csv`，无新依赖。

## 实测样例行（2026-10-07 接前复测）

```csv
adm0_name,adm1_name,mkt_name,cm_name,cur_name,um_name,mp_month,mp_year,mp_price
Afghanistan,Badakhshan,Fayzabad,Bread - Retail,AFN,KG,1,2014,50.0
```

- `package_show` HTTP 200，1 个 CSV 资源，现行 URL 与上表一致。
- 资源 HTTP 200，`text/csv`，UTF-8（带 BOM 可能，按 `utf-8-sig` 读）。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `country` | `adm0_name` | 国家名 |
| `market` | `mkt_name` | 市场名 |
| `commodity` | `cm_name` | 品种+购销环节（如 `Bread - Retail`） |
| `month` | `mp_year`+`mp_month` | 归一为 `YYYY-MM`（零填充） |
| `price` | `mp_price` | 当月市场价（本地货币，float；非数即脏行跳过） |
| `currency` | `cur_name` | ISO 币种码（如 `AFN`） |
| `unit` | `um_name` | 计价单位（如 `KG`） |
| `url` | 解析出的资源 URL | 行级来源可追溯 |
| `source` | 常量 | `wfp-food-price` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 参数白名单与容错

- 仅发两个已实测参数：`package_show` 的 `id=wfp-food-prices`（即工单 `brief.source_urls`
  页面 slug）；资源 URL 由 API 现场解析，不硬编码旧路径，无其他任何参数。
- **流式解析**：全库 CSV 约 200MB，`urlopen` 逐行喂 `csv.reader`，取满 `limit` 行即断流，
  绝不整文件落地。
- **失败即红**：资源解析/下载是单链路，任一环节整体失败抛 `RuntimeError`，不静默返回空
  列表；流中个别脏行（列数不足/价格非数/月份非法）跳过并在异常信息留痕。
- 常规浏览器 UA；不做指纹伪装、频次对抗与验证码处理——遇风控升级按协议转人工。

## 历史深度

1990-01 起至 2021-08（数据集 `dataset_date` 与 `last_modified` 为证，归档停更）。
故 golden 可锚**固定历史月值**且跨期恒定。

## golden 断言口径（跨期稳定）

`golden/001-wfp-food-price.json`（`limit=1`）锚文件首行固定历史值：
`country=Afghanistan / market=Fayzabad / commodity=Bread - Retail / month=2014-01 /
price=50.0 / currency=AFN`，以及 `min_rows: 1`、`source` 常量。**绝不锚**当期/近期值与
`scraped_at`（`whitelist_fields` 收录 `scraped_at`/`timestamp`）。

## 自检

```bash
python3 spiders/wfp-food-price/spider.py          # 冒烟：流式取 3 行打印
python3 scripts/check_manifest_commands.py        # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-wfp-food-price-a8b9bfd6.yaml
```
