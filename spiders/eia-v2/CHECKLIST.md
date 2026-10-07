# eia-v2 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`eia-v2`；入口 `run_eia_v2`）
- [x] `spider.py` 提供 `run_eia_v2(limit=N) -> list[dict]`，只依赖标准库（`urllib`/`json`/`re`/`datetime`，无新依赖）
- [x] **密钥纪律**：key 仅从 `os.environ["EIA_API_KEY"]` 读取；缺 key → 显式 `RuntimeError`（禁止裸跑）；
      不硬编码、不落文件/日志/行数据；行内 `url` 与报错信息均脱敏（`api_key=***`）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] `site` 未写（只访问 api.eia.gov 公开 REST，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门
- [x] columns 与行字段一一对齐（period/respondent/respondent_name/series_type/series_type_name/value/unit/url/source/scraped_at）
- [x] golden 样本 `golden/001-eia-v2.json`（`min_rows: 16`，只锚**固定历史窗口** 2020-01-01T00..03 的冻结值 + 结构常量；绝不当期锚）
- [x] 坑位落实：数值字符串转数、`total` 字符串、5000 行/请求硬顶 + `offset` 尾部分页、
      截断判据 `len(data) < total`、首连重试 1 次、403 鉴权错误显式抛错、窗口空结果即红
- [x] 本地自检：`health_verify.py --ticket reports/health-tickets/20261007-eia-v2-b55acdfb.yaml` → `verdict: ok`
- [x] 仓级自检：`validate_manifests.py` / `check_manifest_commands.py` / `conformance_gate.py --roots .` 全绿
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule`（hourly，如 `"17 * * * *"`）提交——**人工门，spider-heal 不点亮**
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身