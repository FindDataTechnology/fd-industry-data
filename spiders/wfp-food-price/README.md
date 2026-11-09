# wfp-food-price —— WFP 全球市场粮价月度（HDX 活数据集）

新源接入档案（`templates/new-source/`）。工单：`reports/health-tickets/20261006-wfp-food-price-a8b9bfd6.yaml`
（`kind=generate`，拷问 Q1 裁决纳入：官方统计口径）。

## 2026-11-08 迁移：从冻结库换到活库

| | 旧（已废） | 新（现行） |
|---|---|---|
| 数据集 slug | `wfp-food-prices` | **`global-wfp-food-prices`** |
| `last_modified` | 2021-08-22（归档停更） | **2026-11-05** |
| 现行资源 | HTTP **202 + 空体**（读不到表头） | 40 个按年分片 CSV，均 200 |
| 列名 | `adm0_name`/`mkt_name`/`cm_name`/`mp_*` | **全换**：`countryiso3`/`date`/`market`/`commodity`/`price`/`usdprice` … |

旧库里 `spider.py` 已把 202 空体判定写进异常文案；迁移后该分支不再可达。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 分片解析 | `GET https://data.humdata.org/api/3/action/package_show?id=global-wfp-food-prices` | CKAN JSON；`resources[]` 里 **40 个 CSV，一年一个**，名称形如 `Global WFP food prices 2026` |
| 分片本体 | `…/resource/<rid>/download/wfp_food_prices_global_<year>.csv` | HDX 先 302 到 S3 预签名地址（`urlopen` 自动跟随）。2026: 42MB / 2025: 60MB / 2024: 61MB …全量 ≈620MB |

入口：`run_wfp_food_price(limit=2000, window_start=None, window_end=None, years=None, include_non_food=False) -> list[dict]`
（`spider.py`）。仅标准库 `urllib/json/csv/datetime`，无新依赖。

**只下需要的年份**：`years` 缺省 = 当年 + 上一年；不拉 40 年全档。

## 实测表头与样例行（2026-11-08）

```
countryiso3,date,admin1,admin2,market,market_id,latitude,longitude,category,
commodity,commodity_id,unit,priceflag,pricetype,currency,price,usdprice
```

```csv
AFG,2026-10-15,Badakhshan,Argo,Badakhshan,3690,37.04,70.46,cereals and tubers,Bread,55,KG,aggregate,Retail,AFN,77,1.19
```

## 字段口径

| 列 | 来源列 | 口径 |
|---|---|---|
| `country_iso3` | `countryiso3` | **只有 ISO3，新 schema 无国家名**（要中文名由柏讯侧实体表补） |
| `month` | `date` | 归一 `YYYY-MM` |
| `date` | `date` | 观测日 `YYYY-MM-DD`（多为当月 15 日） |
| `category` | `category` | 六大食品类目；`non-food`（汇率/燃油/工资）**默认剔除** |
| `commodity` | `commodity` | 品种名 |
| `market` / `admin1` | 同名 | 市场 / 一级行政区 |
| `price_type` | `pricetype` | `Retail` / `Wholesale` |
| `price_flag` | `priceflag` | `actual`（直接观测）/ `aggregate`（市场月聚合） |
| `price` | `price` | 本币价 |
| `usd_price` | `usdprice` | WFP 给的美元等价 |
| `currency` / `unit` | 同名 | `AFN` / `KG`、`8 pcs`、`240 G`、`2.1 KG`、`46 KG` |
| `url` | — | 该行来自哪个年份分片 |
| `source` / `scraped_at` | — | 常量 / 抓取时刻 |

## 两个必须知道的口径事实

1. **分片按「国家 → 日期」排序**，所以 `limit` 截断得到的是**按字母序的一段国家**，
   不是随机抽样，也不是全球均匀样本。要看某个完整月份，必须
   `window_start=window_end=<YYYY-MM>` 且把 `limit` 放大到 ≥ 该月行数
   （实测 2026-10 = **5763 行 / 16 国**，`limit=100000` 约 24s）。
2. **`non-food` 默认不出**：汇率、柴油、非熟练工工资三行属综合物价，不是粮价。
   需要时显式 `include_non_food=True`。

## 容错

- **失败即红**：分片解析/下载/表头缺列（缺 `countryiso3`/`date`/`commodity`/`price`
  任一）→ `RuntimeError`，不静默返空。
- 脏行（列数不足 / 日期非 ISO / 价格非数）跳过并计数，日志给 `skipped`。
- 常规浏览器 UA；不做指纹伪装与频次对抗。

## golden 断言口径

`golden/001-wfp-food-price.json`：窗口钉 `2026-10`（该分片的完整末月；2026-11 仍在补数），
`limit=100000` 无截断 → 实测 5763 行。锚真实行：`AFG Bread 77.0 AFN（USD 1.19）`、
IRQ 单位 `8 pcs`、SLV `Wholesale`、KEN/NGA 现价、`date=2026-10-15`。

`price`/`usd_price` **不进白名单**：上游回溯修订会让金样转红——这是 canary 语义，
按「上游改了数」处理，不当误报。

## 历史深度

按年分片覆盖 1990 起；本单元**只为当年前后两年取数**，历史回补用
`years=[2019,2020]` 之类显式声明，一次一片。

## 自检

```bash
cd fd-industry-data
.venv/bin/python spiders/wfp-food-price/spider.py     # 冒烟：流式取 3 行
.venv/bin/python scripts/validate_manifests.py
.venv/bin/python scripts/conformance_gate.py
```
