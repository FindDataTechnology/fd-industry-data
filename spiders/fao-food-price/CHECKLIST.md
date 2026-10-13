# fao-food-price 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`fao-food-price`；入口 `run_fao_food_price`）
- [x] `spider.py` 提供 `run_fao_food_price(limit=N, scope=...) -> list[dict]`，依赖 = 标准库 `urllib`/`csv`/`re` + 共享层 `fd_industry_data.tabular`（无单元新依赖）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] `site` 未写（只访问 www.fao.org 公开页面/CSV，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门
- [x] golden 样本 `golden/001-fao-food-price.json`（锚 1990-01 深历史固定月值）
- [x] 本地自检：`health_verify.py --ticket reports/health-tickets/20261006-fao-food-price-d98f85a4.yaml` → `verdict: ok`
- [x] 仓级自检：`validate_manifests.py` / `check_manifest_commands.py` / `conformance_gate.py` 全绿
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick，Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule`（monthly 节奏）提交——**人工门，spider-heal 不点亮**
- [x] **实际（缩减）指数 XLSX 已接入**（2026-10-13，经平台共享层 `fd_industry_data.tabular`）——`scope="annual"` 路径取 `FFPI_Historical` 表（1961+ 年度 名义/实际 双列）；原先「超出本单元标准库直解析范围」的障碍由共享层消除
- [x] 月度路径行为不变：golden 001 重放 diffs 空（100 行）；新增 golden 002 锚年度序列（132 行）
- [x] 依赖说明更新：现为「标准库 + 共享层 `fd_industry_data.tabular`」（该层随包进镜像，非单元新依赖）
