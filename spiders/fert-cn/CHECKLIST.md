# 新源接入检查清单（unified-source-onboarding）—— fert-cn

- [x] 目录名 = manifest `name`（`fert-cn` → 入口 `run_fert_cn`）
- [x] `spider.py` 提供 `run_fert_cn(limit=50, window_start=None, window_end=None) -> list[dict]`，
      只用标准库（urllib/re/html），**未加任何新依赖**、不 OCR
- [x] manifest 字段齐全：name/label/source_url/data_period/functions/columns/concepts/entities/fetch
- [x] `authority: industry` 已标（**商业门户不是协会，绝不标 association**）
- [x] `site` 未写（缺省 tencent）；国内源直连，spider 内 `ProxyHandler({})` 显式禁代理
- [x] **不写 schedule**——先静默合入，跑通再点亮
- [x] 本地自检全绿：`validate_manifests.py` / `conformance_gate.py` / `py_compile`
- [x] 真跑一次（2026-11-08，400 行覆盖 2026-01…08），
      `golden/001-fert-cn.json` 锚 2026-08 尿素出口三值，**无编造**
- [x] golden 通过 `replay_sample` 重放（窗口钉数据月 2026-08）
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`，红则修完再推）
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule`（月更）
- [x] 首爬回补：入口签名显式声明 `window_start`/`window_end`，过滤**数据月**
      而非发布日，**先过滤后截断**
- [x] 边界如实声明：vip 会员区的产量/出厂价不接；本序列**不得作预警判据**
