# imf-pcps —— IMF 初级商品价格体系（PCPS）

新源接入档案。工单：`reports/health-tickets/20261006-imf-pcps-fc45b1b5.yaml`
（`kind=generate`，brief 侦察簿 W1-A 深潜）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 序列取数 | `GET https://api.imf.org/external/sdmx/2.1/data/PCPS/<key>` | 公开 SDMX 2.1 REST，**免 key**，**只返 XML**（`format` 参数被忽略） |

入口：`run_imf_pcps(limit=100, series=None) -> list[dict]`（`spider.py`）。
仅用标准库 `urllib` + `xml.etree`，无新依赖。

## key 口径（与 brief 的偏差修正，实测证据）

- **key 是 4 段** `COUNTRY.INDICATOR.DATA_TRANSFORMATION.FREQ`（读 DSD
  `datastructure/IMF.RES/DSD_PCPS` 实测维度顺序即此）。
- brief `source_urls` 里的 `M.G001.PCOIL.INDEX.M` 是 **5 段**，实测 HTTP 400：
  `key ... has more than expected 4 dimension(s)`。
- 实测可用 key：`G001.PCOIL.INDEX.M`（World=G001 非 W00；PCOIL=原油；INDEX=指数；
  M=月度），返回 417 个 obs（1992-M01 起）。
- 可扩其他商品码：换 `INDICATOR`（如 PCOM 总指数）或 `DATA_TRANSFORMATION`
  （如 USD 价）即可，入口 `series` 参数直通。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `period` | `Obs/@TIME_PERIOD` | SDMX 月期格式（如 `2020-M01`） |
| `value` | `Obs/@OBS_VALUE` | 指数值；上游全精度浮点（如 `68.79415721418565`）**round 6 位**归一 |
| `series_key` | 入参 | 完整四段 key，行级可追溯 |
| `country` / `indicator` / `data_transformation` / `frequency` | `Series/@*` | 序列维度上下文（G001 / PCOIL / INDEX / M） |
| `url` | 请求 URL | 行级来源可追溯 |
| `source` | 常量 | `imf-pcps` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 坑位与容错（brief.notes 逐条落实）

- **旧 `dataservices.imf.org` 已死**：本单元只打 `api.imf.org`。
- **只返 XML**：`format` 参数被忽略；用 `xml.etree` 按**本地名**找 `Series`/`Obs`
  （IMF 返回的这两个元素无命名空间前缀，按前缀找会扑空——已踩过并修正）。
- **错 key 返空数据集不报错**：HTTP 200 + 无 `Series` 的空 `DataSet`。本单元把空序列
  计为该 key 失败并留痕；**全部序列失败抛 `RuntimeError`**（失败即红，不静默返回空
  列表，避免「空洞通过」）。
- **`limit` 语义**：行数上限，期次升序后保留**最近** `limit` 期；golden 用小 `limit`
  锚固定历史 obs → 重放恒定。
- 免 key、免 UA 伪装；不做指纹伪装与频次对抗，遇风控升级按协议转人工，不逆向。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 历史深度与节奏

种子序列 1992-M01 起（417 个月 obs，2026-10 实测），月度更新（当月更新滞后数日）。

## 实测样例行与证据（2026-10-07 抓取）

```
Series COUNTRY="G001" INDICATOR="PCOIL" DATA_TRANSFORMATION="INDEX" FREQUENCY="M"
  <Obs TIME_PERIOD="1992-M01" OBS_VALUE="49.67571279878192" .../>
  <Obs TIME_PERIOD="2020-M01" OBS_VALUE="68.79415721418565" .../>
```
→ `{"period": "2020-M01", "value": 68.794157, "series_key": "G001.PCOIL.INDEX.M", ...}`

## 自检

```bash
python3 spiders/imf-pcps/spider.py          # 冒烟，打印最近 3 行 obs
python3 scripts/check_manifest_commands.py     # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-imf-pcps-fc45b1b5.yaml
```

## golden 断言口径（跨期稳定）

`golden/001-imf-pcps.json` 锚**固定历史 obs**：`period == 2020-M01` 及其
`value == 68.794157`（PCPS 定档历史指数值，round 6 位），外加常量标签
`series_key` / `source` 与 `min_rows: 100`。**绝不锚**最近月份（当月/上月值可能滞后
更新）；`scraped_at`/`timestamp` 进 `whitelist_fields`。
