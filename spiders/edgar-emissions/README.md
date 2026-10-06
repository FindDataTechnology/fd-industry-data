# edgar-emissions —— EDGAR 全球碳排放（JRC 开放目录，按册取）

新源接入档案（`templates/new-source/`）。工单：`reports/health-tickets/20261006-edgar-emissions-3e495c53.yaml`
（`kind=generate`，brief 侦察簿 W3）。

## 数据表面

| 用途 | 端点 | 大小（2026-10-06 实测） |
|---|---|---|
| 年度册（默认） | `GET https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/EDGAR/datasets/EDGAR_2026_GHG/IEA_EDGAR_CO2_1970_2025.zip` | ~4.7 MB |
| 月度册（显式指定才取） | `GET https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/EDGAR/datasets/EDGAR_2026_GHG/IEA_EDGAR_CO2_m_1970_2025.zip` | ~71 MB |
| 目录 | `https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/EDGAR/datasets/` | HTML 索引 |

入口：`run_edgar_emissions(limit=100, book="co2_annual") -> list[dict]`（`spider.py`）。
仅用标准库 `urllib / zipfile / xml.etree`，无新依赖。直链 ZIP **无鉴权**、无频次对抗。

## 参数白名单（只发实测过的参数）

- `book`（函数参数，非网络参数）：`co2_annual`（默认）| `co2_monthly`，其余值抛
  `ValueError`。每轮只取**单册**（brief：「文件较大按需取单册」）。
- 目录路径钉在 `EDGAR_2026_GHG` release（brief 指定 v2026）；JRC 升版（如 EDGAR_2027_GHG）
  时需更新 `spider.py` 的 `RELEASE` 与册文件名（README 本行即更新指引）。

## 册内结构（v2026 实测勘误）

**ZIP 里没有 CSV**——v2026 起册内是嵌套 `*.xlsx`（+`_readme.html`）。XLSX 本质是 zip+XML，
本单元用标准库 `zipfile`（双层解包）+ `xml.etree.ElementTree`（iterparse 流式，逐行 clear，
内存不随行数膨胀）解析；`csv` 模块在 v2026 用不上（brief「ZIP+CSV」为旧版印象，此处勘误留档）。

- 工作表：`IPCC 2006`（另有 `IPCC 1996` / `TOTALS BY COUNTRY`，未用）。
- 前 9 行为元信息脚注（Content/Compound/Start year/End year/Unit: **Gg**）。
- 表头行：`IPCC_annex, C_group_IM24_sh, Country_code_A3, Name,
  ipcc_code_2006_for_standard_report, ipcc_code_2006_for_standard_report_name,
  Substance, fossil_bio` + 年度册 `Y_1970..Y_2025` 宽列 / 月度册 `Year + Jan..Dec`。
- 月度册单元格用共享字符串表（`t="s"`），年度册为内联/裸值——两种都处理。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `book` | 参数 | `co2_annual` / `co2_monthly` |
| `year` | `Y_YYYY` 列名 / `Year` 列 | 1970-2025 |
| `month` | `Jan..Dec` 列 | 1-12；年度册恒为 `null` |
| `country_code` / `country_name` | `Country_code_A3` / `Name` | ISO3 码 + 显示名（首行为 `ABW`/`Aruba`） |
| `sector_code` / `sector` | `ipcc_code_2006_for_standard_report(_name)` | IPCC 2006 行业码与名称 |
| `substance` / `fossil_bio` | 同名列 | `CO2`；`fossil`/`bio` |
| `emissions` | 年/月单元格 | Gg；空单元格跳过，绝不造数 |
| `unit` | 册内 Unit 脚注 | `Gg` |
| `url` | 册 URL | 行级来源可追溯 |
| `source` / `scraped_at` | 常量 / 本地 | `edgar-emissions`；UTC ISO8601 |

## 历史深度与行序（golden 复现的关键）

- 年度册 1970-2025（56 年 × 国家 × 行业，文件序首行 = Aruba）；月度册 1970-2025 × 12 月。
- **行序即文件序**：行内年份升序融化（宽转长），到 `limit` 即停——同册同 `limit` 行集
  确定，首行恒为 `ABW 1.A.1.a fossil 1970`（定版历史值）→ golden 重放可复现。

## 坑位与容错（brief.notes 逐条落实）

- **ZIP+CSV → ZIP+XLSX 勘误**：见上，stdlib zipfile+ElementTree 仍在「仅标准库」约束内。
- **按需取单册**：默认轻册（4.7 MB，冒烟全程 ~4 s）；月度册 71 MB 仅 `book="co2_monthly"`
  时才取（下载超时 300 s）。
- **失败即红**：下载失败 / 坏 ZIP / 缺工作表 / 缺表头列 / 0 行解析，一律抛 `RuntimeError`，
  不静默返回空列表。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 实测样例行与证据（2026-10-06T21:14Z）

```
{"book": "co2_annual", "year": 1970, "month": null, "country_code": "ABW",
 "country_name": "Aruba", "sector_code": "1.A.1.a",
 "sector": "Main Activity Electricity and Heat Production",
 "substance": "CO2", "fossil_bio": "fossil", "emissions": 17.343706456412,
 "unit": "Gg", "url": ".../EDGAR_2026_GHG/IEA_EDGAR_CO2_1970_2025.zip",
 "source": "edgar-emissions", "scraped_at": "2026-10-06T21:14:14+00:00"}
```

月度册同构可解析（`book="co2_monthly"`，下载 ~71 MB；接入后建议人工试跑一次确认时效可接受）。

## 自检

```bash
python3 spiders/edgar-emissions/spider.py          # 单册冒烟，打印 3 行长表
python3 scripts/check_manifest_commands.py          # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-edgar-emissions-3e495c53.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-edgar-emissions.json`（`params: {limit: 5}`）只锚**固定历史年定版值**：
`country_code == ABW`、`year == 1970`、`sector_code == 1.A.1.a`、
`emissions == 17.343706456412`（Aruba 1970 年电力行业化石 CO2，历史定版）、
`source == edgar-emissions`、`unit == Gg`，以及 `min_rows: 5`。
**绝不锚**当期/近年值与抓取时间；`scraped_at`/`timestamp` 进 `whitelist_fields`。
