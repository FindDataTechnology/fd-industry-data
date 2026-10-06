# fao-food-price 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`fao-food-price`；入口 `run_fao_food_price`）
- [x] `spider.py` 提供 `run_fao_food_price(limit=N) -> list[dict]`，只依赖标准库 `urllib`/`csv`/`re`（无新依赖）
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
- [ ] 实际（缩减）指数 XLSX 解析立项（`food_price_index_nominal_real.xlsx`，zip+xml 标准库可解，
      本批未接）另行推进，不阻塞爬取本身
