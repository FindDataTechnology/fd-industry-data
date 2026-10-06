# fao-empres 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`fao-empres`；入口 `run_fao_empres`）
- [x] `spider.py` 提供 `run_fao_empres(limit=N, start_date, end_date) -> list[dict]`，只依赖标准库 `urllib`/`csv`（无新依赖）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] `site` 未写（只访问 api.data.apps.fao.org / data.apps.fao.org 公开表面，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门
- [x] golden 样本 `golden/001-fao-empres.json`（固定历史窗口 2023-01，锚深历史聚合计数）
- [x] 本地自检：`health_verify.py --ticket reports/health-tickets/20261006-fao-empres-2fd55009.yaml` → `verdict: ok`
- [x] 仓级自检：`validate_manifests.py` / `check_manifest_commands.py` / `conformance_gate.py` 全绿
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick，Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule`（weekly 节奏）提交——**人工门，spider-heal 不点亮**
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身
