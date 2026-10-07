# statcan-bulk —— 加拿大统计局 bulk CSV（NHPI 新房价指）

新源接入档案（`templates/new-source/` 范式，范本 `spiders/nmc-weather/`）。工单：
`reports/health-tickets/20261006-statcan-bulk-2bf4fdf6.yaml`（`kind=generate`，brief 侦察簿 W2-5）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 数据 | `GET https://www150.statcan.gc.ca/n1/tbl/csv/18100205-eng.zip` | 官方 bulk ZIP（约 355 KB）＝ `18100205.csv`（约 9.3 MB 长表）+ `18100205_MetaData.csv` |

入口：`run_statcan_bulk(limit=100, product_id="18100205") -> list[dict]`（`spider.py`）。
标准库 `urllib` + `zipfile` + `csv`，无新依赖。

## 鉴权与参数白名单

- 无 key；带常规浏览器 UA。
- `product_id` 只放行 8 位数字形态（bulk 命名口径，连字符形式 18-10-0205 自动归一）；
  默认 `18100205`。productId 以表目录为准（工单 brief）：18-10-0205-01 =
  *New housing price index, monthly*（CANSIM 327-0056，现行）。

## 字段口径（工单 brief：ref_date/geography/value）

| 列 | 来源 | 口径 |
|---|---|---|
| `ref_date` | `REF_DATE` | `YYYY-MM`（月度） |
| `geography` | `GEO` | 地理名（如 `Canada`） |
| `series` | 第 4 列维度 | 表内系列名（NHPI 为 `Total (house and land)` / `House only` / `Land only`） |
| `uom` | `UOM` | `Index, 201612=100` |
| `value` | `VALUE` | float；空串缺测 → `None` |
| `url` / `source` / `scraped_at` | — | 行级可追溯 / `statcan-bulk` / UTC ISO8601 |

## 坑位与容错（brief.notes 逐条落实）

- **WDS REST 是坏的**：POST `getAllCubesList` 实测 HTTP 405、GET 形态可用但本单元不依赖
  WDS，直接走 bulk ZIP；productId 以表目录 + ZIP 内 MetaData 实测为准
  （18100205 现行，数据至 2026-08）。
- **ZIP→CSV 标准库**：`zipfile.ZipFile(io.BytesIO(...))` + `csv.DictReader`；
  CSV 为 UTF-8 with BOM，必须 `utf-8-sig` 解码。
- **建筑许可表取舍**：brief 点名的建筑许可现行表 34-10-0292-01
  （*Building permits, by type of structure and type of work*，月度）bulk ZIP 实测
  **368 MB**，远超单元承载（旧的 026-00xx 系已停更于 2017-12），本单元未纳入，仅接
  工单 source_urls 指定的 NHPI 表；后续如需建筑许可，建议以 WDS 按需筛选或单独建单元。
- **行序稳定**：CSV 按 REF_DATE 升序、新月份追加尾部；头部行 = 1981-01 Canada
  Total (house and land) = 38.2（Index 201612=100），跨月刷新不漂移。
- **失败即红**：ZIP 下载 / 解包 / 找不到主 CSV / 解析 0 行 → `RuntimeError`，不静默返回空列表。
- **无 schedule**：manifest 刻意不写 `schedule`，静默合入后再由人工点亮。

## 历史深度

1981-01 起，月度，现行至 2026-08（ZIP 内文件实测 2026-09-17 刷新）。

## 实测样例行（2026-10-06 GET 证据）

```
GET https://www150.statcan.gc.ca/n1/tbl/csv/18100205-eng.zip
→ 18100205.csv 首行：1981-01, Canada, 2016A000011124, Total (house and land),
   Index 201612=100, VALUE=38.2；次行 House only=36.1；全表 65,760 行
```

## 自检

```bash
python3 spiders/statcan-bulk/spider.py          # 单页冒烟，打印前 3 行
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-statcan-bulk-2bf4fdf6.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-statcan-bulk.json` 用 `params={"limit":3}`（头部三行恒定：1981-01 Canada
三个系列），锚**固定表固定月值**：`ref_date==1981-01 && geography==Canada &&
series=="Total (house and land)" && value==38.2`。**绝不锚** 尾部当期月份（随月刷新）
与 `url` 之外的滚动字段；`scraped_at`/`timestamp` 进 `whitelist_fields`。
