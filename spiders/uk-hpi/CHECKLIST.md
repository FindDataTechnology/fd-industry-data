# uk-hpi 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`uk-hpi`；入口 `run_uk_hpi`）
- [x] `spider.py` 提供 `run_uk_hpi(limit=N) -> list[dict]`，只依赖标准库（无新依赖）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] `site` 未写（只访问 publicdata.landregistry.gov.uk 官方固定文件，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门
- [x] golden 样本 `golden/001-uk-hpi.json`（`min_rows: 3`，锚固定 region 固定历史月数值）
- [x] 本地自检：`health_verify.py --ticket reports/health-tickets/20261006-uk-hpi-48ae2c94.yaml` → `verdict: ok`
- [x] 仓级自检：`validate_manifests.py` / `conformance_gate.py --roots .` / `check_manifest_commands.py` 全绿
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）——**人工门，spider-heal 不点亮**
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身
