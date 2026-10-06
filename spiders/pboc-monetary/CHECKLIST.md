# pboc-monetary 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`pboc-monetary`；入口 `run_pboc_monetary`）
- [x] `spider.py` 提供 `run_pboc_monetary(limit=N) -> list[dict]`，只依赖标准库 `urllib/zipfile/xml.etree/json`（无新依赖）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] `site` 未写（只访问 www.pbc.gov.cn 公开静态页与 xlsx，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门
- [x] golden 样本 `golden/001-pboc-monetary.json`（锚已公布即冻结的历史月值，非当月）
- [x] 工单 brief 旧路径 2168/2639 已 404 复测确认，改用现行栏目 116219/116319（README 有证）
- [x] 本地自检：`health_verify.py --ticket reports/health-tickets/20261006-pboc-monetary-ad2a8dc3.yaml` → `verdict: ok`
- [x] 仓级自检：`validate_manifests.py` / `conformance_gate.py --roots .` / `check_manifest_commands.py` 全绿
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）——**人工门，spider-heal 不点亮**
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身
