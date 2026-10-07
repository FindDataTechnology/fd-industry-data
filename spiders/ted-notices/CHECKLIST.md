# ted-notices 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`ted-notices`；入口 `run_ted_notices`）
- [x] `spider.py` 提供 `run_ted_notices(limit=N) -> list[dict]`，只依赖标准库 `urllib`（无新依赖）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] `site` 未写（只访问 api.ted.europa.eu 公开 REST，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门
- [x] golden 样本 `golden/001-ted-notices.json`（固定历史日期段，只锚 date/source/首条 notice_id）
- [x] 本地自检：`health_verify.py --ticket reports/health-tickets/20261006-ted-notices-0438b7ca.yaml` → `verdict: ok`
- [x] 仓级自检：`validate_manifests.py` / `conformance_gate.py --roots .` / `check_manifest_commands.py` 全绿
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）——**人工门，spider-heal 不点亮**
- [ ] 匿名配额观察：上游 429 较频，若常态化建议注册 API key（另行人工决策，不含密钥入库）
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身
