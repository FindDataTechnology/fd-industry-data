# edgar-emissions 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`edgar-emissions`；入口 `run_edgar_emissions`）
- [x] `spider.py` 提供 `run_edgar_emissions(limit=N) -> list[dict]`，只依赖标准库 `urllib/zipfile/xml.etree`（无新依赖）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] `site` 未写（只访问 jeodpp.jrc.ec.europa.eu 公开目录，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门
- [x] golden 样本 `golden/001-edgar-emissions.json`（`min_rows: 5`，锚 1970 年 ABW 定版值）
- [x] 本地自检：`health_verify.py --ticket reports/health-tickets/20261006-edgar-emissions-3e495c53.yaml` → `verdict: ok`
- [x] 仓级自检：`validate_manifests.py` / `conformance_gate.py --roots .` / `check_manifest_commands.py` 全绿
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] （建议）月度册 `book="co2_monthly"`（71 MB）人工试跑一次，确认平台内存/时长配额可接受
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）——**人工门，spider-heal 不点亮**
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身
- [ ] JRC 升版（EDGAR_2027_GHG）时更新 `spider.py` 的 `RELEASE` 与册文件名
