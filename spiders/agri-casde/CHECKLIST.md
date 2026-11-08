# 新源接入检查清单（unified-source-onboarding）—— agri-casde

- [x] 目录名 = manifest `name`（`agri-casde` → 入口 `run_agri_casde`）
- [x] `spider.py` 提供 `run_agri_casde(limit=50, window_start=None, window_end=None) -> list[dict]`，
      只用标准库（urllib/html.parser/re/json），**未加任何新依赖**、不 OCR
- [x] manifest 字段齐全：name/label/source_url/data_period/functions/columns/entities/fetch
- [x] `site` 未写（缺省 tencent）；国内源直连，spider 内 `ProxyHandler({})` 显式禁代理
- [x] **不写 schedule**——先静默合入，跑通再点亮
- [x] 本地自检全绿：`validate_manifests.py` / `conformance_gate.py` / `py_compile`
- [x] 真跑一次（2026-11-08，2000 行 = No.119–124 六期 × 5 表），
      `golden/001-agri-casde.json` 锚的全部数值取自该次真实产出，**无编造**
- [x] golden 通过 `fd_industry_data.health.guard.replay_sample` 重放
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`，红则修完再推）
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule`（月更，每月 9–12 日发布 → 建议每月 12 日之后调度）
- [x] 首爬回补：入口签名显式声明 `window_start`/`window_end`，按期次月过滤且
      **先过滤后截断**（同 `abs-sdmx` 惯例）
- [ ] 指标语义另行走统一指标注册目录治理（`concepts` 已给
      `agriculture.supply_demand_balance`；`unit: mixed` 需在注册目录按品类拆分）
