# usda-psd —— USDA FAS PSD 全球农产品供需平衡表（keyed 源）

新源接入档案（`templates/new-source/`）。工单：
`reports/health-tickets/20261007-usda-psd-9f3fb5e1.yaml`（`kind=generate`，
批次二 Wave B 直建）。

## 数据表面（全部实测于 2026-10-07）

| 用途 | 端点 | 实测 |
|---|---|---|
| 商品目录（起点） | `GET https://api.fas.usda.gov/api/psd/commodities` | 200，63 个商品；**必须复数 `commodities`，单数实测 404** |
| 国家目录 | `GET /api/psd/countries` | 200，251 个国家/地区（`countryCode` 如 `US`/`BR`/`CH`） |
| 属性目录 | `GET /api/psd/commodityAttributes` | 200，85 个属性（28 Production、88 Exports、176 Ending Stocks、184 Yield…） |
| 单位目录 | `GET /api/psd/unitsOfMeasure` | 200，42 个单位（8=`(1000 MT)`、4=`(1000 HA)`、26=`(MT/HA)`…） |
| **取数（records 端点族）** | `GET /api/psd/commodity/{commodityCode}/country/{countryCode}/year/{marketYear}` | 200；`0440000`+`US`+`2020` → **15 行**（该商品/国家/市场年**最新一期修订**快照） |
| 全量变体 | `country` 传 `all` | 200；Corn MY2020 → 1875 行（全部国家 + 完整修订历史） |

入口：`run_usda_psd(limit=100, commodity="0440000", country="US", market_year="2020",
with_names=True) -> list[dict]`（`spider.py`）。仅用标准库 `urllib`/`json`/`re`。

路径族逐一实测的负例（写进代码防漂移）：`/api/psd/commodity`、
`/api/psd/commodity/0440000`、`/api/psd/commodity/0440000/year/2020`、
`/api/psd/commodity/0440000/country/US` 均 **404**；有效形态只有
`/commodity/{c}/country/{co}/year/{y}`。`/api/psd/attributes`、`/api/psd/units` 实测
404（真名是 `commodityAttributes` / `unitsOfMeasure`）。

**密钥纪律**：api.data.gov key 只从 `os.environ["DATA_GOV_API_KEY"]` 读（集群由
secret `fd-industry-source-keys` 经 `envFrom` 注入，见
`k8s/cronjob-template/templates/cronjob.yaml`），走 `X-Api-Key` **请求头**；缺 key
立即抛 `RuntimeError`（绝不回退 `DEMO_KEY`）。key 不入 URL——行内 `url` 列天然无
密钥；密钥不进任何产出行、日志或 golden 文件。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `commodity_code` / `commodity_name` | `commodityCode` + `/commodities` | 商品码与名（目录富化，失败/关闭为 `None`） |
| `country_code` / `country_name` | `countryCode` + `/countries` | 国家码与名（同上） |
| `market_year` | `marketYear` | PSD 市场年（1960 起） |
| `calendar_year` / `month` | `calendarYear` / `month` | 该行所属**发布期**（修订戳；最新一期快照） |
| `attribute_id` / `attribute_name` | `attributeId` + `/commodityAttributes` | 平衡表属性（28 Production、88 Exports、125 Domestic Consumption、176 Ending Stocks、184 Yield…） |
| `unit_id` / `unit` | `unitId` + `/unitsOfMeasure` | 计量单位（8 `(1000 MT)`、4 `(1000 HA)`、26 `(MT/HA)`…） |
| `value` | `value` | 属性数值（行内单位见 `unit`） |
| `url` | 请求 URL | 行级可追溯，**无 key**（key 在请求头） |
| `source` | 常量 | `usda-psd` |
| `scraped_at` | 本地 | UTC ISO8601，抓取时刻 |

## 坑位与容错（工单 brief.notes / 实测逐条落实）

- **配额 1,000 次/时/key，且跨 api.data.gov 全平台代理 API 合并计算**（超限临时
  封禁）：每次运行 1 个数据请求 + ≤4 个目录请求（目录结果进程内缓存，重复调用不
  重发）；串行、间隔 0.5s、不并发、不重试放大。默认调用一次约 5 个请求。
- **缺 key / 无效 key**：实测 HTTP 403 + `{"error":{"code":"API_KEY_MISSING"}}` /
  `{"code":"API_KEY_INVALID"}`；另有纯文本 `Bad API Key` 口径。响应体首字符非
  `{`/`[` 报错；JSON 带 `error` 键同样报错。
- **未知代码不报错**：未知 commodity/country 实测 HTTP 200 + `[]`。空结果视为该
  分片可疑失败（跳过留痕）——PSD 有效组合自 1960 起恒有数据，空列表通常意味着
  代码有误；**全部分片失败/全空抛 `RuntimeError`**，不静默返回空列表。
- **未知年度 404**：实测 `year/1900` → 404（客户端先做 1960..来年 范围校验，
  快速失败且省配额）。
- **单页失败跳过留痕**：单分片失败向 stderr 打印并继续；目录富化失败只降级为
  名字列 `None`，不影响数据行。
- **最新一期快照语义**：`country/{co}/year/{y}` 返回该市场年**最新修订**的行
  （实测 MY2020 快照发布期 = 2025-07）；需要修订历史时用 `country=all`
  （每行自带 `calendar_year`/`month` 发布期）。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`（Wave B 直建统一静默合入，
  点亮留人工门）。

## 自检

```bash
python3 spiders/usda-psd/spider.py           # 冒烟，打印默认口径 3 行
python3 scripts/validate_manifests.py         # 双 lint 之一
python3 scripts/check_manifest_commands.py    # 双 lint 之二（命令漂移）
python3 scripts/health_verify.py --ticket reports/health-tickets/20261007-usda-psd-9f3fb5e1.yaml
python3 scripts/conformance_gate.py --roots .
```

## golden 断言口径（跨期稳定）

`golden/001-usda-psd.json`（`params={"limit":30}`）只锚**默认口径**下的确定性与
定值：

- 结构与常量：`commodity_code=0440000`、`commodity_name=Corn`、`country_code=US`、
  `country_name=United States`、`market_year=2020`、`attribute_id=184`、
  `attribute_name=Yield`、`unit_id=26`、`unit=(MT/HA)`、`source=usda-psd`、
  `url`（逐字符，无 key）；
- **冻结定值**：美国玉米 MY2020 单产 `value == 10.7608`（MT/HA，构建时实况冻结；
  同快照 Production=357819、Exports=69775）；
- `calendar_year`/`month`（修订戳）**刻意不锚**——上游若再修订 MY2020 会更新发布
  期与数值，由自愈环按 `sample-update-pending-human` 处置；
- `scraped_at` / `timestamp` 进 `whitelist_fields`。

`limit=30` 覆盖默认口径全部 15 行，锚定行必在返回集中且与行序无关。

## 实测样例行（2026-10-07 冒烟，实况冻结）

```json
{"commodity_code": "0440000", "commodity_name": "Corn", "country_code": "US",
 "country_name": "United States", "market_year": 2020, "calendar_year": 2025,
 "month": 7, "attribute_id": 184, "attribute_name": "Yield", "unit_id": 26,
 "unit": "(MT/HA)", "value": 10.7608,
 "url": "https://api.fas.usda.gov/api/psd/commodity/0440000/country/US/year/2020",
 "source": "usda-psd", "scraped_at": "2026-10-07T11:31:13+00:00"}
```

默认口径实测 15 行（4 Area Harvested、20 Beginning Stocks、28 Production、
57 Imports、86 Total Supply、88 Exports、125 Domestic Consumption、
176 Ending Stocks、184 Yield…）；`country=all` MY2020 实测 1875 行。