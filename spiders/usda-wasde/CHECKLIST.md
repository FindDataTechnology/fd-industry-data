# 新源接入检查清单（unified-source-onboarding）—— usda-wasde

- [x] 目录名 = manifest `name`（`usda-wasde` → 入口 `run_usda_wasde`）
- [x] `spider.py` 提供 `run_usda_wasde(limit=200, window_start=None, window_end=None, months_back=6)`，
      只用标准库（urllib/re/datetime），**未加任何新依赖**、不 OCR
- [x] manifest 字段齐全：name/label/source_url/data_period/functions/columns/concepts/entities/fetch
- [x] `site` 未写（海外直连）；spider 内 `ProxyHandler({})` 显式禁代理
- [x] **不写 schedule**——先静默合入，跑通再点亮
- [x] 本地自检全绿：`validate_manifests.py` / `conformance_gate.py` / `py_compile`
- [x] 真跑一次（2026-11-08，2 期 × 480 行 = 960 行），
      `golden/001-usda-wasde.json` 锚的全部数值取自该次真实产出，**无编造**
- [x] golden 通过 `fd_industry_data.health.guard.replay_sample` 重放（窗口钉 2026-10）
- [x] **道歉页坑已实现并实测**：未发布期 = 200 + 369B `text/html`，
      content-type + 体量 + 期号行三重判定（实测 `wasde1126.txt` 被正确判为未发布）
- [x] 表边界按分隔线计数界定；品类标签先剥脚注再判数字；地区用三档白名单
      （三处均为实测踩到后修掉的真缺陷）
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`，红则修完再推）
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule`（月更，9—12 日发布 → 建议每月 12 日之后调度）
- [x] 首爬回补：入口签名显式声明 `window_start`/`window_end`，按**发布月**过滤、
      **先过滤后截断**
- [x] 历史深度如实声明：索引页只挂当前期与下期、归档页 403 → **短窗源（最近 2 期）**，
      不伪造长序列
