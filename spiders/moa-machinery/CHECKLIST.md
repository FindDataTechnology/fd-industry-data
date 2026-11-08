# 新源接入检查清单（unified-source-onboarding）—— moa-machinery

复制本目录到 `spiders/<my-source>/`，逐项勾掉。全程只有 git 提交与平台自助操作——
不新建仓库、不发镜像、不碰常驻组件（这是 openspec crawl-platform 的契约）。

- [x] 目录名 = manifest `name`（`moa-machinery` → 入口 `run_moa_machinery`）
- [x] `spider.py` 提供 `run_moa_machinery(limit=20, window_start=None, window_end=None) -> list[dict]`，
      只用标准库（urllib/re/html/ssl），**未加任何新依赖**、不 OCR
- [x] manifest 字段齐全：version/name/label/source_url/data_period/functions/columns/entities/fetch
- [x] `site` 未写（缺省 tencent）；国内源直连，spider 内 `ProxyHandler({})` 显式禁代理
- [x] **不写 schedule**——年度源先静默合入，cron 后续单独决策
- [x] 本地自检全绿：
      `python3 scripts/validate_manifests.py && python3 scripts/conformance_gate.py --roots .`
      以及 `python3 -m py_compile spiders/moa-machinery/spider.py`
- [x] 真跑一次（2026-11-08，76 行 = 4 期 × 19 行），`golden/001-moa-machinery.json`
      锚的全部数值取自该次真实产出，**无编造**
- [x] golden 通过 `fd_industry_data.health.guard.replay_sample` 重放
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`，红则修完再推）
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule` 提交（年度源建议 `M H D * *`，发布日期集中在 9–11 月）
- [x] 知悉首爬回补（industry-crawl-gitops）：入口签名显式声明 `window_start`/`window_end`，
      可被 kwargs 注入；按公报**发布日期**过滤且先过滤后截断。注：年度源按「滚动 12 个
      整月」窗口只会命中最新一期，历史期需不带窗口运行（默认取全部可见期）
- [ ] 需要指标语义时，另行走统一指标注册目录治理（列→绑定→verified→标识符），
      不阻塞爬取本身（本单元 `concepts` 刻意留空：item/value/unit 长表跨量纲）
