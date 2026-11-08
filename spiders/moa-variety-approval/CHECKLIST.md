# 新源接入检查清单（unified-source-onboarding）—— moa-variety-approval

- [x] 目录名 = manifest `name`（`moa-variety-approval` → 入口 `run_moa_variety_approval`）
- [x] `spider.py` 只用标准库（urllib / re / html），**未加任何新依赖**
- [x] manifest 字段齐全：name/label/source_url/authority/data_period/functions/columns/entities/fetch
- [x] **`concepts: []`**——事件面单元，**不声明任何数值概念**（不伪装成有观测）
- [x] `authority: official` 已标（农业农村部种业管理司本级发布）
- [x] `data_period: irregular`（公示公告无固定周期）
- [x] `site` 未写（缺省 tencent）；国内源直连，spider 内 `ProxyHandler({})` 显式禁代理
- [x] **不写 schedule**——先静默合入，跑通再点亮
- [x] **只登记事件不写数值观测**：详情页 `<table>` 数 = 0，品种清单在 `.doc` 附件
      （`zxfile/reader` 代理）内；不解析 `.doc`、不 OCR（2026-10-13 裁决未动）
- [x] **附件存在性不猜**：默认 `with_detail=False` → `has_attachment`/`attachment_count`
      /`variety_list_in_attachment` 一律 `null`（未知记未知，不写 `False`）
- [x] 真跑一次（2026-11-08，`limit=40` → 40 事件 / 3 页；类型分布
      provincial_variety_approval 18 / seed_license 14 / other 6 / national 2）
- [x] 两处缺陷已修并复验：发布日期从锚点文本摘出（不再空、不再粘标题）、
      「农牧厅」类省份匹配（内蒙古自治区 ✓）
- [x] `golden/001-moa-variety-approval.json` 锚窗口 2026-11 的真实 6 条，**无编造**
- [x] golden 通过 `fd_industry_data.health.guard.replay_sample` 重放
- [x] 首爬回补：入口声明 `window_start`/`window_end`，按发布日期过滤、**先过滤后截断**
- [x] 翻页终止如实处理：`index_99` = 404，以 404 停；不假设固定页数
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`，红则修完再推）
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule`（公示公告建议 每周）
