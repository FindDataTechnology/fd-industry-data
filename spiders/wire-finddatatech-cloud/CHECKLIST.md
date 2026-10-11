# 新源接入检查清单（unified-source-onboarding）

单元：`spiders/wire-finddatatech-cloud/`（工单 `20261011-wire-DE-20261011-99`，kind=generate）

- [x] 目录名 = manifest `name`（连字符目录的入口函数把 `-` 换 `_`：`run_wire_finddatatech_cloud`）
- [x] `spider.py` 提供 `run_wire_finddatatech_cloud(limit=N) -> list[dict]`，只依赖镜像预装依赖
      （本单只用标准库 `urllib`/`json`——端点返回纯 JSON，无需 scrapling/httpx，未加新依赖）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/entities/fetch
- [x] `site` 未写（走默认执行位 tencent；未离开默认站点）
- [x] **未写 schedule**——静默合入，点亮由人工补写（`brief.cadence: daily`）
- [x] golden 样本 ≥1：`golden/001-wire-health.json`（断言只锚 `service`/`url` 等跨期稳定字段）
- [x] 本地自检全绿：
      `python3 scripts/validate_manifests.py && python3 scripts/conformance_gate.py --roots .`
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`，红则修完再推）—— 由 PR 审阅侧确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）
- [ ] 需要指标语义时，另行走统一指标注册目录治理（列→绑定→verified→标识符），
      不阻塞爬取本身

> 本单元是演练单（drill）：源端点可被 drill 侧随时关闭。关闭后取数显式失败 → 验证链判红 →
> 走修复/转人工流程，不产出占位假数据。
