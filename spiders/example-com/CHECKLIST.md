# 新源接入检查清单（unified-source-onboarding）

单元：`spiders/example-com/`（工单 `20261012-wire-DE-20261012-99`，kind=generate）

- [x] 目录名 = manifest `name`（连字符目录的入口函数把 `-` 换 `_`：`run_example_com`）
- [x] `spider.py` 提供 `run_example_com(limit=N) -> list[dict]`，只依赖镜像预装依赖
      （本单只用标准库 `urllib`/`html.parser`——静态 HTML 无需 scrapling/httpx，未加新依赖）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/entities/fetch
- [x] `site` 未写（走默认执行位；未离开默认站点）
- [x] **未写 schedule**——静默合入，点亮由人工补写（`brief.cadence: daily`）
- [x] golden 样本 ≥1：`golden/001-example-com.json`（断言只锚 `title`/`url`/`source` 等跨期稳定字段）
- [x] 本地自检全绿：
      `python3 scripts/validate_manifests.py && python3 scripts/conformance_gate.py --roots .`
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`，红则修完再推）—— 由 PR 审阅侧确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）
- [ ] 需要指标语义时，另行走统一指标注册目录治理（列→绑定→verified→标识符），
      不阻塞爬取本身

> 本单元是演练单（drill）：工单来自 wire 需求单 `DE-20261012-99`，仅用于验证
> 「需求→出单→生成→验证→PR」生产链。verdict 只以验证链为准，不因 drill 而放宽。
