# 新源接入检查清单（unified-source-onboarding）—— moa-fishery

- [x] 目录名 = manifest `name`（`moa-fishery` → 入口 `run_moa_fishery`）
- [x] `spider.py` 提供 `run_moa_fishery(limit=50, window_start=None, window_end=None) -> list[dict]`，
      只用标准库（urllib/re/html），**未加任何新依赖**、不 OCR
- [x] manifest 字段齐全：name/label/source_url/data_period/functions/columns/concepts/entities/fetch
- [x] `site` 未写（缺省 tencent）；国内源直连，spider 内 `ProxyHandler({})` 显式禁代理
- [x] **不写 schedule**——先静默合入，跑通再点亮
- [x] 本地自检全绿：`validate_manifests.py` / `conformance_gate.py` / `py_compile`
- [x] 真跑一次（2026-11-08，115 行 = 年度 25 + 月度累计 90），
      `golden/001-moa-fishery.json` 锚的全部数值取自该次真实产出，**无编造**
- [x] golden 通过 `replay_sample` 重放（窗口钉 2025 年，同时命中两种粒度）
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`，红则修完再推）
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule`（月更；年内累计口径 → 月中之后调度）
- [x] 首爬回补：入口签名显式声明 `window_start`/`window_end`，按**数据期**按月过滤
      （年度行按 12 月计入窗口），**先过滤后截断**
- [x] 粒度分离：`cumulative_month` 与 `year` 显式标注，**不合并、不内插**
- [x] 价格序列未接（侦察未在栏目看到）——如需另找官方源，不在本单元臆造
