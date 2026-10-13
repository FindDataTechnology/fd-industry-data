# fao-food-price —— FAO 食品价格指数（月度 CSV + 年度 XLSX，总指数+分品类）

新源接入档案（`templates/new-source/`）。工单：`reports/health-tickets/20261006-fao-food-price-d98f85a4.yaml`
（`kind=generate`，拷问 Q1 裁决纳入：官方统计口径非行情）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 官方页 | `GET https://www.fao.org/worldfoodsituation/foodpricesindex/en/` | 页内 data 链接发现现行 CSV 地址 |
| CSV（页内现行） | `https://www.fao.org/media/docs/worldfoodsituationlibraries/wfs-library/food_price_indices_data.csv?sfvrsn=<stamp>&download=true` | 2026-10-07 实测 HTTP 200，48 KB，445 行 |
| CSV（固定无参） | `https://www.fao.org/media/docs/worldfoodsituationlibraries/wfs-library/food_price_indices_data.csv` | 同日实测 200 且与版本戳形态字节一致（48110 B） |
| XLSX（页内现行） | `.../food_price_index_nominal_real.xlsx?sfvrsn=<stamp>` | 2026-10-13 实测 HTTP 200，25 KB，sheet `FFPI_Historical` |
| XLSX（固定无参） | `.../food_price_index_nominal_real.xlsx` | 同日实测 200 且同内容（25014 B） |

入口：`run_fao_food_price(limit=100, scope="monthly") -> list[dict]`（`spider.py`）。

`scope` 取值：`monthly`（缺省，行为不变）= 月度 CSV；`annual` = 同页 XLSX 的年度序列；
`all` = 两者合并。依赖 = 标准库 `urllib`/`csv`/`re` + **平台共享层
`fd_industry_data.tabular`**（读 XLSX；该层随包进镜像，非单元新依赖）。

## 链接策略（brief「接前复测现行链接」落实）

`sfvrsn` 是 FAO 附件版本戳，重新上传即变：spider 先抓官方页正则发现现行 CSV 链接
（`href="...food_price_indices_data.csv..."`，去 HTML 转义），发现失败再落固定无参 URL——
两种形态实测同内容，双保险。

## CSV 布局与解析

非表格 CSV：头两行说明（`FAO Food Price Index` / `2014-2016=100`）→ 表头行（首格 `Date`）→
空行 → 数据行（首格 `YYYY-MM`）。解析器先定位 `Date` 表头，再收 `YYYY-MM` 行；数据块结束后
再遇非空非数据行即停（防未来追加异构块串味）。

## 字段口径（宽表 → 长表）

| 列 | 来源 | 口径 |
|---|---|---|
| `month` | 数据行首格 | 月度行 = `YYYY-MM`（1990-01 起至当月）；**年度行 = `YYYY`**（1961 起） |
| `index_name` | 表头列名 | 月度：`Food Price Index` / `Meat` / `Dairy` / `Cereals` / `Oils` / `Sugar`（官方英文原名）；年度：`Food Price Index (nominal)` / `Food Price Index (real)` |
| `value` | 数据格 | 指数值（现行基期 2014-2016=100）；空值格跳过不造数。年度行含名义与实际（缩减）两条序列 |
| `url` / `source` / `scraped_at` | 常量/本地 | 追溯与标识 |

`limit` = 返回行数上限：按（month, index_name）字典序确定性排序后截取（缺省前 100 行 ≈
1990 年头 16 个月 × 6 指数）。

## 坑位与容错（brief.notes 逐条落实）

- **官方统计口径**：接的是 FAO 官方月度指数（统计口径），非市场行情——用途定位按拷问 Q1 裁决。
- **名义 vs 实际（2026-10-13 已补齐）**：工单 expectations 提「名义与实际」。月度 CSV
  **只有名义块**；实际（缩减）指数在同页 XLSX 的 `FFPI_Historical` 表——实测该表是**年度**
  序列（1961–2026，列 = `Month`/`Nominal`/`Real`；**首格虽标 `Month` 实为年份**，故按 4 位
  数字校验而非信表头文字）。此前判为「超出本单元标准库直解析范围」而不接；**平台共享层
  `fd_industry_data.tabular` 上线后该障碍消除**，故新增 `scope="annual"` 路径（经共享层读表，
  表头列号按 `Nominal`/`Real` 实读、不写死列序）。**月度路径行为完全不变**（golden 001
  重放 diffs 空）。年度行复用 `month` 列承载 4 位年份、`index_name` 带 `(nominal)`/`(real)`
  后缀——两者都是「期间键 + 序列名 + 值」的长表模型，故同列复用是刻意的。
- **基期重述风险**：FAO 换基期（如 2014-2016=100）会重述全部历史值，golden 深锚届时会红——
  属可见失败（非空洞），按 README 口径换锚即可。
- **仅常规 UA**：免鉴权站点，不做指纹伪装、不做频次对抗。
- **失败即红**：页面发现与数据面两跳，任何一跳失败或 0 数据行 → 抛 `RuntimeError`，不静默回空。
  年度路径失败**不影响**月度路径（月度是主数据面，`scope="monthly"` 不触碰 XLSX）。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 实测样例行（2026-10-07 冒烟，排序前 6 = 1990-01 全部指数）

```json
{"month": "1990-01", "index_name": "Cereals", "value": 64.1}
{"month": "1990-01", "index_name": "Food Price Index", "value": 64.4}
{"month": "1990-01", "index_name": "Oils", "value": 44.59}
```

## 自检

```bash
python3 spiders/fao-food-price/spider.py          # 单页冒烟，打印 1990-01 的 6 行指数
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-fao-food-price-d98f85a4.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-fao-food-price.json` 用 `limit=100` 重放（缺省窗口即 1990 年头段），锚
`1990-01 / Food Price Index / value=64.4`（36 年前深历史，回填已收敛；每条规则独立匹配任一行）。
**绝不锚** 当月值；`scraped_at`/`timestamp` 进 `whitelist_fields`。基期重述会使深锚可见变红——
按上节口径处理，不属静默漂移。

## 年度路径实测样例行（2026-10-13，`scope="annual"`）

```json
{"month": "1961", "index_name": "Food Price Index (nominal)", "value": 20.351527128964303}
{"month": "1961", "index_name": "Food Price Index (real)", "value": 104.33692291961376}
{"month": "2026", "index_name": "Food Price Index (real)", "value": 112.21804486729721}
```

年度表实测 419 行（含表头与空行），有效数据 1961–2026；`scope="annual", limit=200`
实测产出 132 行（66 年 × 2 序列）。
