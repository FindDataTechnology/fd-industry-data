# usda-quickstats 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`usda-quickstats`；入口 `run_usda_quickstats`）
- [x] `spider.py` 提供 `run_usda_quickstats(limit=N) -> list[dict]`，只依赖标准库 `urllib`/`json`/`re`（无新依赖）
- [x] **key 从 `os.environ["QUICKSTATS_API_KEY"]` 读**，缺 key 抛明确 `RuntimeError`；密钥不硬编码、不进任何文件/日志/行（至多尾四位，本单元不落任何片段）
- [x] 行内 `url` 列剥离 key（golden 亦无 key）
- [x] 响应体首字符校验 `{`/`[`（非 JSON 报错）+ JSON `error` 键报错（`unauthorized` 实测 401）
- [x] 单次 ≤50,000 行：达上限按截断失败处理（跳过留痕），大查询按 `years`/`state` 分片
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch；`columns` 与 spider 行字段逐一对齐
- [x] `site` 未写（只访问 quickstats.nass.usda.gov 官方 API，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门
- [x] golden 样本 `golden/001-usda-quickstats.json`（`params.limit=20`，锚 2020 冻结定值 90432000 + 常量 + url）
- [x] 本地自检：`health_verify.py --ticket reports/health-tickets/20261007-usda-quickstats-6fa3d4d0.yaml` → `verdict: ok`
- [x] 仓级自检：`validate_manifests.py` / `check_manifest_commands.py` / `conformance_gate.py --roots .` 全绿
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出（集群需已注入 `QUICKSTATS_API_KEY`）
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）——**人工门，spider-heal 不点亮**
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身