# 候选清单对账摘要（2026-11-21）

来源：`data-source-scouting/candidates.json`（派生）+ `candidates.overrides.json`（手写判断）。
判据：`scripts/check_candidates_reconciliation.py`。

## 三档（accepted 必须闭合）

| 档 | 数 | 含义 |
|---|---|---|
| landed_as | 113 | 指到仓内真实单元 |
| never_landed | 33 | 从未落地，进待接队列 |
| dropped | 0 | 曾落地后退役 |
| **合计** | 146 / accepted 146 | 闭合 |

## never_landed（待接队列）

- `水利部水雨情` —— （无 note）
- `搜猪网生猪大数据` —— （无 note）
- `高德开放平台` —— （无 note）
- `生意社-100ppi` —— （无 note）
- `深圳开放平台` —— （无 note）
- `data-gov-uk` —— （无 note）
- `data-gov-目录` —— （无 note）
- `opensky` —— （无 note）
- `treasury-fiscaldata` —— （无 note）
- `kosis-openapi` —— （无 note）
- `nbs-流通领域生产资料价格` —— （无 note）
- `四川统计局` —— （无 note）
- `kosis-零售-线上购物表` —— （无 note）
- `data-europa-eu-sparql` —— （无 note）
- `world-bank-data360-mcp` —— （无 note）
- `pboc-区域金融` —— （无 note）
- `理财产品查询` —— （无 note）
- `国家林草局` —— （无 note）
- `花讯网` —— （无 note）
- `expoflores-厄瓜多尔` —— （无 note）
- `kenya-flower-council` —— （无 note）
- `flws-1-800-flowers-com` —— （无 note）
- `1377-t-sakata-seed` —— （无 note）
- `丰乐种业-000713` —— （无 note）
- `邻接候选` —— （无 note）
- `data-gov-目录-1285` —— （无 note）
- `data-gov-uk-ckan` —— （无 note）
- `opendatasoft-1301` —— （无 note）
- `data-europa-eu-sparql-1333` —— （无 note）
- `四川统计局-1334` —— （无 note）
- `treasury-fiscaldata-1511` —— （无 note）
- `data-gov-uk-1512` —— （无 note）
- `opendatasoft-1513` —— （无 note）

## needs_review 的理由分布（机械导出）

- 43 条：缺 endpoint；缺鉴权性质；缺 vertical
- 8 条：字段齐备但抽取器判为歧义（需人看原文）
- 8 条：缺 vertical
- 7 条：多端点混写
- 7 条：缺鉴权性质
- 5 条：缺鉴权性质；缺 vertical
- 5 条：缺 endpoint；缺鉴权性质
- 4 条：多端点混写；缺鉴权性质
- 2 条：一行多接口；多端点混写
- 2 条：一行多接口；缺鉴权性质
- 2 条：多端点混写；缺鉴权性质；缺 vertical
