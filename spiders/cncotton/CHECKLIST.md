# 新源接入检查清单（unified-source-onboarding）—— cncotton

- [x] 目录名 = manifest `name`（`cncotton` → 入口 `run_cncotton`）
- [x] `spider.py` 提供 `run_cncotton(limit=30, window_start=None, window_end=None)`，
      只用标准库（urllib/re）+ 平台共享层 `fd_industry_data.pdftext`，**未加任何新依赖**
- [x] manifest 字段齐全：name/label/source_url/data_period/functions/columns/concepts/entities/fetch
- [x] **`source_url` 用 `http://`**（443 从未部署，https 直接 RST）
- [x] `authority: industry` 已标（研究机构转述 USDA 预测，非官方统计发布、非协会）
- [x] `site` 未写（缺省 tencent）；国内源直连，spider 内 `ProxyHandler({})` 显式禁代理
- [x] **不写 schedule**——先静默合入，跑通再点亮
- [x] 本地自检全绿：`validate_manifests.py` / `conformance_gate.py` / `py_compile`
- [x] 真跑一次（2026-11-08，`limit=200` → 176 行 / 36 期，覆盖 2022-08…2025-08），
      `golden/001-cncotton.json` 锚 2025-08 十项，全部逐字对照 PDF 文本层，**无编造**
- [x] golden 通过 `fd_industry_data.health.guard.replay_sample` 重放
- [x] **PDF 走文本层不 OCR**：`pdftext` 明确不做 OCR；图片型 PDF 空串 → 跳过并计数；
      `pdftotext` 缺失 → 直接抛错（失败即红，不静默回空表）
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`，红则修完再推）
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule`（月更）
- [x] 首爬回补：入口声明 `window_start`/`window_end`，按报告期过滤、**先过滤后截断**
- [x] 停更如实声明：站点最后一期 2025-08，**不伪造之后的期次**；点亮后交健康巡检分诊
