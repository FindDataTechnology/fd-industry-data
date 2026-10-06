# boxofficemojo —— Box Office Mojo 年度票房排行

新源接入档案（`templates/new-source/`）。工单：`reports/health-tickets/20261006-boxofficemojo-c200b654.yaml`
（`kind=generate`，拷问 Q3 裁决纳入：文娱口径）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 年度排行 | `GET https://www.boxofficemojo.com/year/<YYYY>/` | 公开 HTML 单表，免鉴权；年份覆盖 1977 起 |

入口：`run_boxofficemojo(limit=100) -> list[dict]`（`spider.py`）。仅标准库
`urllib/re/html/json`，无新依赖。本单元取**最近三个年度页**（当年进行中 + 两个已完成
年，新年份在前）；brief.source_urls 指定的 2024 页为其中冻结历史年。

## 实测样例行（2026-10-07 接前复测）

| year | rank | title | gross (USD) |
|---|---|---|---|
| 2024 | 1 | Inside Out 2 | 652,980,194 |
| 2024 | 2 | Deadpool & Wolverine | 636,745,858 |
| 2026 | 1 | Spider-Man: Brand New Day | 954,528,523（当年进行中，随周度更新） |

三个年页均 HTTP 200，各 200 行，表结构一致。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `year` | 年页 URL | 排行年份 |
| `week` | 常量 | 年度行恒为 `null`；周度页（`/week/YYYYWnn/`）接入时填 ISO 周号 |
| `rank` | `mojo-field-type-rank` 格 | 1 = 年度最高票房 |
| `title` | `mojo-field-type-release` 格内链接文本 | `html.unescape` 还原（`&amp;` → `&`） |
| `gross` | 行内**第一个非 hidden** 的 `mojo-field-type-money` 格 | 年度 gross，去 `$`/逗号取整（hidden 的 Budget 格负向排除） |
| `url` | 年页 URL | 行级来源可追溯 |
| `source` | 常量 | `boxofficemojo` |
| `scraped_at` | 本地 | UTC ISO8601 |

## 参数白名单与容错

- 仅直连 `https://www.boxofficemojo.com/year/<YYYY>/`（YYYY ∈ 最近三年），无任何查询参数
  （排序参数 `?sort=…` 未实测不发）。
- 行内 rank/release/money 三要素齐全才算数据行——表头排序行、布局行自动跳过。
- **失败即红**：单年页失败只跳过留痕；全部年页失败抛 `RuntimeError`；无重试放大。
- 直连低频（weekly cadence，3 页/次 ≈ 1.4MB）；常规浏览器 UA，不做指纹伪装——
  遇风控升级按协议转人工。

## 历史深度

1977 起各年页同构（本单元固定最近 3 年）；更深年份把 `YEAR_WINDOW` 调大即可。

## golden 断言口径（跨期稳定）

`golden/001-boxofficemojo.json`（`limit=600` 覆盖三页全行）锚**冻结历史年页数值**：
`year=2024` 页 `rank=1 / title=Inside Out 2 / gross=652980194`，及 rank2
`gross=636745858`；另锚 `source` 结构位与 `min_rows: 590`。**绝不锚**进行中年（2026）
的 rank/gross（随周度上映变动）。注意：约 15 个月后（2028 年起窗口滚出 2024）按口径
刷新锚（正常维护，非故障）。`scraped_at`/`timestamp` 进 `whitelist_fields`。

## 自检

```bash
python3 spiders/boxofficemojo/spider.py          # 冒烟：取 3 行打印
python3 scripts/check_manifest_commands.py        # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-boxofficemojo-c200b654.yaml
```
