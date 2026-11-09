# D 桶待定清单（67 个源）

任务 5.1 的产物：成功运行次数 <5 的源，**不凭猜声明**（spec: 「样本不足时不得凭猜声明」），
进本清单等样本长起来再判。数据来自 `docs/min-rows-buckets-2026-11-09.csv`（近 90 天，
`status='success'`）。

判据：`n < 5`。阈值一旦有 ≥5 次成功，就能算 p10/p50 并判桶，届时按 A/B 桶口径补声明。

## 样本数分布

| n（近 90 天成功次数） | 源数 |
|---|---|
| 4 | 14 |
| 3 | 20 |
| 2 | 12 |
| 1 | 21 |
| **合计** | **67** |

## 清单（按样本数降序）

| source | n | zero | p10 | p50 | 本仓有 manifest |
|---|---|---|---|---|---|
| abs-sdmx | 4 | 0 | 44 | 100 | 是 |
| afdc | 4 | 0 | 44 | 100 | 是 |
| clinicaltrials | 4 | 0 | 44 | 100 | 是 |
| dbnomics-api | 4 | 0 | 44 | 300 | 是 |
| dounan-flower-price | 4 | 0 | 350 | 932 | 是 |
| flower-auction | 4 | 4 | 0 | 0 | 是 |
| jodi-oil | 4 | 0 | 44 | 100 | 是 |
| mem-disaster | 4 | 0 | 12 | 12 | 是 |
| mofcom-data | 4 | 0 | 44 | 100 | 是 |
| nfra-penalty | 4 | 0 | 100 | 100 | 是 |
| oecd-cli | 4 | 0 | 44 | 100 | 是 |
| openalex-works | 4 | 0 | 38 | 81 | 是 |
| usda-wasde | 4 | 0 | 100 | 100 | 是 |
| worldsteel | 4 | 1 | 6 | 21 | 是 |
| auth-smoke | 3 | 0 | 1 | 1 | 是 |
| census-eits | 3 | 0 | 14 | 14 | 是 |
| dce | 3 | 1 | 20 | 100 | 是 |
| ecb-exr | 3 | 0 | 36 | 100 | 是 |
| fert-cn | 3 | 0 | 100 | 100 | 是 |
| flk-law-backfill | 3 | 3 | 0 | 0 | **否** |
| gleif-lei | 3 | 0 | 32 | 81 | 是 |
| miit-gxsj | 3 | 0 | 36 | 100 | 是 |
| nbs-housing | 3 | 0 | 36 | 100 | 是 |
| nifdc-batchcert | 3 | 0 | 36 | 100 | 是 |
| openfda | 3 | 0 | 36 | 100 | 是 |
| ourairports | 3 | 0 | 7 | 7 | 是 |
| pboc-inclusive | 3 | 0 | 36 | 100 | 是 |
| pboc-monetary | 3 | 0 | 36 | 100 | 是 |
| sc-gd-stats | 3 | 0 | 36 | 100 | 是 |
| spb-express | 3 | 0 | 36 | 100 | 是 |
| statcan-bulk | 3 | 0 | 36 | 100 | 是 |
| twse-monthly-revenue | 3 | 0 | 35 | 100 | 是 |
| uk-hpi | 3 | 0 | 36 | 100 | 是 |
| unido-sdmx | 3 | 0 | 36 | 100 | 是 |
| census-m3 | 2 | 0 | 16 | 16 | 是 |
| edgar-emissions | 2 | 0 | 28 | 60 | 是 |
| ember | 2 | 0 | 85 | 85 | 是 |
| hf-datasets | 2 | 0 | 1 | 1 | 是 |
| ico-coffee-price | 2 | 0 | 52 | 156 | 是 |
| jp-estat | 2 | 0 | 87 | 87 | 是 |
| mct-star-hotel | 2 | 0 | 938 | 4291 | 是 |
| mct-tourism-demand | 2 | 0 | 64 | 64 | 是 |
| restaurant-rpi | 2 | 0 | 6 | 11 | 是 |
| sec-edgar-8k-operating | 2 | 0 | 20 | 20 | 是 |
| usda-psd | 2 | 0 | 15 | 15 | 是 |
| wfp-food-price | 2 | 0 | 100 | 100 | 是 |
| agri-casde | 1 | 0 | 100 | 100 | 是 |
| aiph-stats | 1 | 0 | 81 | 81 | 是 |
| chinabond-yield-curve | 1 | 0 | 100 | 100 | 是 |
| chinamoney-ncd-curve | 1 | 0 | 100 | 100 | 是 |
| eastmoney-disclosure-plan | 1 | 0 | 100 | 100 | 是 |
| education_cc_courses | 1 | 0 | 2 | 2 | 是 |
| education_oa_fulltext | 1 | 0 | 20 | 20 | 是 |
| education_oer_aggregators | 1 | 0 | 2 | 2 | 是 |
| education_openalex | 1 | 0 | 20 | 20 | 是 |
| education_podcast | 1 | 0 | 2 | 2 | 是 |
| fred-cpi | 1 | 0 | 1780 | 1780 | 是 |
| iea-ev | 1 | 0 | 16 | 16 | 是 |
| jp-foodservice-trend | 1 | 0 | 147 | 147 | 是 |
| moa-fishery | 1 | 0 | 40 | 40 | 是 |
| moa-machinery | 1 | 0 | 38 | 38 | 是 |
| moa-variety-approval | 1 | 0 | 74 | 74 | 是 |
| polygon | 1 | 1 | 0 | 0 | **否** |
| samr-food-inspection | 1 | 0 | 100 | 100 | 是 |
| shclearing-capital-notes | 1 | 0 | 100 | 100 | 是 |
| tencent-smoke | 1 | 0 | 1 | 1 | 是 |
| tpex-monthly-revenue | 1 | 0 | 893 | 893 | 是 |

其中 2 个在本仓没有 manifest 目录（`flk-law-backfill`, `polygon`），
它们的声明得落在各自的产品仓里，不是这里。

## 复审时机

不要按日历复审——按样本长起来复审。建议直接在巡检侧加一条提示：某源成功次数达到 5 时，
在巡检摘要里点名"可判定"，避免这份清单躺成死档。
