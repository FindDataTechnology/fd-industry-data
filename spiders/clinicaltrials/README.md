# clinicaltrials —— ClinicalTrials.gov 全球临床试验注册月度量

新源接入档案（`templates/new-source/`）。工单：`reports/health-tickets/20261006-clinicaltrials-941ede99.yaml`
（`kind=generate`，批次二，侦察簿 W2-2）。

## 数据表面

| 用途 | 端点 | 说明 |
|---|---|---|
| 月度量 | `GET https://clinicaltrials.gov/api/v2/studies?countTotal=true&filter.advanced=AREA%5BStudyFirstPostDate%5DRANGE%5B<首日>,<末日>%5D&pageSize=1` | 公开 REST v2，**免 key**，`totalCount` 一次返回整月注册量，`studies[0]` = 抽样研究 |

入口：`run_clinicaltrials(limit=24, start_month=None, end_month=None) -> list[dict]`
（`spider.py`）。仅用标准库 `urllib`，无新依赖。

## 参数白名单（只发实测过的参数）

`countTotal=true` / `filter.advanced`（`AREA[StudyFirstPostDate]RANGE[起,止]`，方括号与
逗号 URL 编码）/ `pageSize=1` —— 其余不发。

## 口径坑位（实测证据）

- **filter 参数名**：工单字面 URL 用 `filter.area=StudyFirstPostDate RANGE[...]`，实测
  HTTP 400 ``filter.area is unknown parameter``；v2 正确写法是
  `filter.advanced=AREA[StudyFirstPostDate]RANGE[起,止]`（`RANGE` 紧贴 `AREA[...]`，
  逗号分隔起止日期，含首尾）。
- **口径核对**：2020-01 实测 totalCount=2867；2024-01=3776，与 brief 侦察簿
  「2024-01=3776 实测口径」完全一致。
- **数据近实时**：实测 2026-10-06 时 2026-09 完整月已有量（4216），无 openFDA 式
  索引滞后；缺省窗口 = 截至上个完整自然月的近 `limit` 个月。
- **0 结果语义**：v2 对空结果返回 404，本单元按该月 totalCount=0 透出（注册量月度
  均在数千，真实 0 月仅可能出现在 2000 年以前，不构成风险）。

## 字段口径

| 列 | 来源 | 口径 |
|---|---|---|
| `month` | 派生 | 自然月 ISO `yyyy-mm`（StudyFirstPostDate 落月） |
| `total_count` | `totalCount` | 该月注册（首发帖）研究量（int） |
| `sample_nct_id` | `studies[0].protocolSection.identificationModule.nctId` | 抽样研究 NCT ID（索引更新排序可能变，故入 whitelist） |
| `url` / `source` / `scraped_at` | 请求 URL / 常量 / 本地 | 行级溯源 |

## 历史深度与节奏

- 数据 **2000 年起**（brief 口径）；历史月 totalCount 发布后冻结。
- `frequency: monthly`；缺省取最近 `limit` 个完整月，历史由调度正向累积。

## 容错

- 单月失败只跳过并留痕；**全部月份失败抛 `RuntimeError`**（失败即红）。
- `limit=24` 仅 24 请求，远低于站点容量；不重试、不加频次、无鉴权。
- **无 schedule**：`manifest.yaml` 刻意不写 `schedule`，静默合入后再由人工点亮。

## 自检

```bash
python3 spiders/clinicaltrials/spider.py          # 冒烟，打印最近 3 个完整月
python3 scripts/check_manifest_commands.py         # manifest 命令漂移 lint
python3 scripts/health_verify.py --ticket reports/health-tickets/20261006-clinicaltrials-941ede99.yaml
```

## 实测样例行（2026-10-06，golden 钉窗口重放）

```json
{"month": "2020-01", "total_count": 2867, "sample_nct_id": "NCT04239820",
 "url": "https://clinicaltrials.gov/api/v2/studies?countTotal=true&filter.advanced=AREA%5BStudyFirstPostDate%5DRANGE%5B2020-01-01,2020-01-31%5D&pageSize=1",
 "source": "clinicaltrials", "scraped_at": "…"}
```

缺省窗口实测（同日）：2026-07=4857 / 2026-08=4263 / 2026-09=4216。

## golden 断言口径（跨期稳定）

`golden/001-clinicaltrials.json` 用 `params` 钉死 2020-01..2020-02 固定历史窗口，锚
**冻结月总量**：`month == 2020-01`、`total_count == 2867`、`source`/`url` 常量、
`min_rows: 1`。**绝不锚** 近月数值、`sample_nct_id`（抽样顺序可能变化，进
`whitelist_fields`）与 `scraped_at`/`timestamp`（在 `whitelist_fields`）。
