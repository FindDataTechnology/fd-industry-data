# limit_kind 审计（2026-11-21）

- 亮着单元 125：rolling 100 / full_set 24 / unassigned 1

## unassigned（缺字段，单列不判）

- `carbon-monitor`

## 已定性、未测量（full_set 但全集规模未实测）

- `afdc`
- `aiph-stats`
- `boxofficemojo`
- `cisa`
- `edgar-emissions`
- `education_cc_courses`
- `education_oer_aggregators`
- `education_openalex`
- `gb-standards`
- `iea-ev`
- `imf-pcps`
- `jodi-gas`
- `jodi-oil`
- `mct-star-hotel`
- `mct-tourism-demand`
- `moa-machinery`
- `mohurd-yearbook`
- `nasa-power`
- `nrdl-national-catalog`
- `ourairports`
- `uk-hpi`
- `usaspending-awards`
- `usgs-nwis`
- `worldsteel`

## 正在截断（full_set 缺 limit）

- `gb-standards`（存量基线内）

## 豁免（定性了但量不了全集）

- `edgar-emissions` —— 年度册一次性发布，全集未测；实测 20 行
- `education_cc_courses` —— OCW sitemap 编目，全集未测但实测 2 行；加 limit 是行为改动，留后续
- `education_oer_aggregators` —— OER 聚合器三通道，全集未测但实测 2 行
- `education_openalex` —— 季度全量分区快照，全集 ~7.6M 但单轮受 max_results 限制，量法与耗时未定；实测 20 行
- `iea-ev` —— 历史统计册，全集未测；实测 16 行
- `moa-machinery` —— 农机统计年报，全集未测；实测 38 行
- `ourairports` —— 全球机场维表 CSV，全集未测但实测 7 行（csv 很大但 spider 只取一页）
- `worldsteel` —— 产品×地区固定表，全集未测；实测 24 行
