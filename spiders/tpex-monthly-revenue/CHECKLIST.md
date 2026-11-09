# tpex-monthly-revenue 接入检查清单

- [x] 目录名 = manifest `name`（`tpex-monthly-revenue`；入口 `run_tpex_monthly_revenue`）
- [x] `spider.py` 只依赖标准库 `urllib`（无新依赖）
- [x] manifest v2 字段齐全：name/label/source_url/schedule/data_period/functions/columns/concepts/entities/fetch
- [x] `site` 未写（只访问 www.tpex.org.tw 公开 OpenAPI，走缺省执行位 tencent）
- [x] `data_period: month`（词表内）
- [x] golden 样本 `golden/001-tpex-monthly-revenue.json`（锚结构 + 觀光餐旅过滤 + 2729 在册，不锚当月数值）
- [x] 本地冒烟：`python3 spiders/tpex-monthly-revenue/spider.py` → 返回 觀光餐旅 行（含 2729 瓦城）
- [ ] 仓级自检：`conformance_gate.py` / `validate_manifests.py` / `check_manifest_commands.py` 全绿
- [ ] push 后 CI gate 绿
- [ ] 等一个 dispatcher tick，Console 源清单出现该源
- [ ] 试跑并核对 crawl_runs 记录与抽取器落库（2729 瓦城 2026-09）
