# aiph-stats（AIPH 国际花卉统计年鉴 · 分品种分国别种植面积）

AIPH（国际园艺生产者协会）与 Union Fleurs 联合出版的《International Statistics — Flowers and
Plants》年鉴，其**公开 XLSX 附件**里的「分品种章节本」。本单元只取其中的**种植面积**表。
入口函数：`run_aiph_stats(limit: int = 2000, editions: int = 1) -> list[dict]`。

这是本线唯一能拿到 **EU 以外**（印度/中国/厄瓜多尔/哥伦比亚/肯尼亚/墨西哥/越南/泰国…）
花卉分品种种植面积的通道；产量口径另有 `eurostat-api`（`apro_cpsh1`）覆盖 EU。

## 端点与取数路径（实测 2026-11-08，UTC+8）

1. **年鉴索引页** `https://aiph.org/giic/international-statistical-yearbook/`（200，248 KB / 267 KB body）
   - 页内列出各年版数据页：`<年>-yearbook-data-links/`，实测有 **2022 / 2023 / 2024 / 2025**。
   - 本单元**按年份取最大**，不硬编码年份；`editions=N` 取最新 N 版。
2. **年版数据页** `.../<年>-yearbook-data-links/`（200，196 KB）
   - 列出该年全部公开 XLSX。**文件名逐年版漂移**（实测）：

     | 年鉴年 | 分品种章节本 |
     |---|---|
     | 2025 | `ISYB_2025_production.xlsx` |
     | 2024 | `SYB_2024_chapter2_products-_pages.xlsx` |
     | 2023 | `SYB_2023_chapter2_products-_pages.xlsx` |

     同页另有 `...Eurostat Trade Tables...xlsx` 与 `...Summary Production and Trade.xlsx`。
   - 挑选规则是**语义的**：路径含 `chapter2` / `products` / `production` 之一，且不含
     `eurostat` / `summary`。这样三年都命中，未来年版只要沿用语义命名即可继续工作。
3. **工作簿**（约 130–140 KB，27 个工作表）
   - 分品种三连表 × 9 个品种：`<品种> Trade` / `<品种> Graph` / `<品种> Production`，
     最后两个是 `Flowering plants area` 与 `Green and Foliage plants area`。
   - 本单元只接 **9 张面积表**，跳过全部 `Trade` 与 `Graph`（见「口径」）。

### ⚠ 传输会被中途截断（本线最大操作坑）

同一 URL 三次下载分别落到 **119679 / 131982 字节**，而 `content-length` 恒为 **136164**；
截断的 xlsx 解压即 `BadZipFile`。因此单元：

1. 下载后比对 `len(body)` 与 `content-length`；
2. 不足则带 `Range: bytes=N-` **续传**（实测两次 206 续传即补齐）；服务器忽略 Range 回 200 时整包重收；
3. 补齐后 `zipfile.ZipFile` + `testzip()` **验完整性**；
4. 仍坏 → **整版跳过并记 warning，绝不部分解析**。

同时实测该站会偶发 40–60 s 卡在 9 KB 无响应（一次复现），因此会话设 `retries=2`
—— 整轮只有 3 个请求（索引页 + 年版页 + 工作簿），不构成重试风暴，但单次失败会让整轮零产出。

## 口径（关键判断）

1. **只做面积，不做贸易。** 同一工作簿的 Trade 表脚注明写
   `Source: Euroststat, provisional data 2024`（例：`Roses Trade` 表尾），并标注
   `Data based on table 3.2. Roses, Imports (CN-Code 06031100)` —— 那是 Eurostat 临时数据的
   二次发布。本仓既有 `eurostat-api` / `un-comtrade` 是一手车道，重复接入只会在贸易板上**双计**。
   面积相反：**EU 以外拿不到**，是真正空白。
2. **每行自带参考年（`ref_year`），不是时间序列。** 实测 2025 版玫瑰花面积：
   India 2023、China 2020、Ecuador 2024、Colombia 2024、**Kenya 2012**、Mexico 2023、Italy 2005。
   单元**绝不把年鉴年（2025）当成数据年**；下游画图必须按 `ref_year` 标注（年鉴本身也是这么用的）。
   `editions=4` 可拿到 2022–2025 四版，**但同一年鉴的多版之间只有被修订过的行才会变**，
   不能当成四条独立年度观测——聚合时要按 `(product, country)` 取 `yearbook` 最大的那条。
3. **表头列序逐表不同，一律按表头文字定位列。** 实测三类：
   - 多数表 `Area in ha | Total | In the open | Under protection | Year`
   - `Lilies Production` 是 `Area in ha | Total | Under protection | In the open | Year`（**保护地在左**）
   - `Christmas trees Production` / `Cut Foliage Production` 只有 `Area in ha | Total | Year`

   按固定列序取数会把「露地」读成「保护地」，所以 `_parse_area_sheet` 只认表头。
4. **国名带脚注上标。** `Netherlands 3` 的 `3` 是脚注序号（表尾 `3 revised data` /
   `3 Area under protection, required…`）。单元先扫表尾收脚注索引，再剥掉国名尾部的同名数字；
   `country_raw` 保留原始串以便回溯。
5. **`Total` 含未分类面积**（表头带脚注上标 `Total 2` → `2 Including unspecified area`），照录。
6. **上游自身不一致：照录不对账。** Orchids 表 `Netherlands 3` 的 `Total` = 80 ha 但
   `Under protection` = 90 ha（保护地大于合计）。单元不修正、不重算、不告警。
7. **品种映射是人工白名单，不做名称猜测。** 工作表名 → 品种键逐条写死，并显式收录上游拼写错误
   `Chysanthemum Prod.`（正确拼法 `Chrysanthemum`）。未知工作表名 → 记 warning 跳过。

## 字段清单（13 列）

| 列 | 说明 |
|---|---|
| `product` | 归一品种键：`rose` / `carnation` / `orchid` / `chrysanthemum` / `lily` / `christmas_tree` / `cut_foliage` / `flowering_plant` / `green_foliage_plant` |
| `product_label` | 章节展示名（如 `Roses`、`Green and foliage plants`） |
| `country` | 剥掉脚注上标的国名（`Netherlands 3` → `Netherlands`） |
| `country_raw` | 上游原样国名 |
| `area_ha` | 种植面积合计（ha，含未分类面积）；缺失为 None，不填 0 |
| `area_open_ha` | 露地面积（ha）；该表无此列时为 None |
| `area_protected_ha` | 保护地面积（ha）；同上 |
| `ref_year` | **该行数据**的参考年（2005–2024 混合），不是年鉴年 |
| `yearbook` | 年鉴年版（如 2025） |
| `source_sheet` | 原始工作表名（如 `Roses Production `，注意尾部空格；`Chysanthemum Prod.` 为上游拼写错误） |
| `workbook_url` | 该版 XLSX 直链 |
| `source` | 固定 `aiph-stats` |
| `scraped_at` | UTC ISO 时间 |

概念绑定：`area_ha / area_open_ha / area_protected_ha` →
`area.flower.planted.{total,open,protected}`，`entity_type=country`、`measure=area`、
`unit=hectare`、`frequency=annual`。

> **词表缺口（挂账）**：`fd-open-data-protocol` 的 `units.yaml` 目前**没有 `hectare`**，
> `measure` 亦无 `area` 先例。波2 注册概念前需先补词表条目（或者在注册时降级为
> `unit: count` + 说明——不推荐，会在量纲上失去意义）。

## 真实取数证据

- 取数时间：**2026-11-08 01:02–01:08（UTC+8）**，`.venv/bin/python`（scrapling + 仓内
  `fd_industry_data.tabular.xlsx` 读表）经 `load_spider_module('aiph-stats','spiders')` 装载实跑。
- **最新版全量**：`run_aiph_stats(limit=5000, editions=1)` → **81 行 / 9 张面积表 / 9 个品种**：

  | 品种 | 行数 |
  |---|---|
  | rose | 17 |
  | chrysanthemum | 16 |
  | carnation | 13 |
  | lily | 10 |
  | christmas_tree | 8 |
  | flowering_plant | 5 |
  | cut_foliage | 4 |
  | orchid | 4 |
  | green_foliage_plant | 4 |

  `area_ha` 无 None、无 0；`ref_year` 覆盖 {2005, 2007, 2008, 2010, 2012, 2013, 2019, 2020,
  2021, 2022, 2023, 2024}；脚注剥离命中 7 处（全部为 `Netherlands 3`）。
- **样例行（未加工）**：

```json
{"product": "rose", "country": "India", "country_raw": "India", "area_ha": 41000.0, "area_open_ha": null, "area_protected_ha": null, "ref_year": 2023, "yearbook": 2025, "source_sheet": "Roses Production ", "source": "aiph-stats"}
{"product": "rose", "country": "Kenya", "country_raw": "Kenya", "area_ha": 2163.8, "area_open_ha": null, "area_protected_ha": null, "ref_year": 2012, "yearbook": 2025, "source_sheet": "Roses Production ", "source": "aiph-stats"}
{"product": "orchid", "country": "Netherlands", "country_raw": "Netherlands 3", "area_ha": 80.0, "area_open_ha": null, "area_protected_ha": 90.0, "ref_year": 2024, "yearbook": 2025, "source_sheet": "Orchids Production ", "source": "aiph-stats"}
```

- **结构跨年稳定**：2024 版（`SYB_2024_chapter2_products-_pages.xlsx`，续传补齐 136164 字节）
  与 2025 版**同为 27 个工作表、工作表名逐字相同**，仅顺序略有差异 ⇒ 解析器不依赖顺序。

## 容错与边界

- `FetcherSession(impersonate="chrome120", timeout=60, verify=False, retries=2)`。
- 索引页不可用 → 记 error 返回空列表（不抛异常给 runner）；年版页不可用 → 跳过该版。
- 工作簿坏包/超时 → 续传 + 验包，仍坏则跳过整版（**绝不部分解析**）。
- 单元格不可解析为数字 → None（不填 0）；`ref_year` 解析不出 → None（行仍保留，面积有效）。
- 行级去重键 `(yearbook, product, country)`。
- 入口读 `fd_industry_data.cancel_event.is_set()`，可被 runner 中途取消。
- **不声明 `window_start` / `window_end`**：年鉴是年度出版物，一版即全年，首爬的「滚动 12 个月
  回补窗」对本源无意义（声明了反而会按窗口过滤掉真实数据）。历史深度由 `editions` 显式控制。

## 版权与署名

年鉴数据版权属 AIPH / Union Fleurs。本单元只做**观测抽取与内部分析**，不转发原始 XLSX；
对外看板引用这些数字时须署名
「来源：AIPH / Union Fleurs, International Statistics — Flowers and Plants（<年版>）」。

## 遗留风险

1. **年版页 URL 模式**（`<年>-yearbook-data-links/`）是当前站点的约定；若 AIPH 改版到别的路径，
   `_year_pages()` 的正则需同步改（会以「索引页无年版链接 → 返回空」显式失败，不会静默出错数）。
2. **章节本命名靠语义匹配**（`chapter2`/`products`/`production`）：若未来年版改成纯哈希文件名，
   `_pick_workbook()` 会取不到 → 跳过该版并记 warning。届时按当年页面实际文件名补规则。
3. **golden 钉了 2025 版的具体值**（India 玫瑰 41000 ha / Kenya 2163.8 ha / ref_year 2012 等）：
   新版年鉴若修订这些数字，golden 会失败——这正是它该做的事（提示人工复核），
   处置方式是核对上游后更新钉值。
4. **`hectare` 不在词表**（见上「词表缺口」），波2 注册前需先补。
