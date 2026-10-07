# census-m3 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`census-m3`；入口 `run_census_m3`）
- [x] `spider.py` 提供 `run_census_m3(limit=N) -> list[dict]`，只依赖标准库 `urllib/json/os`（无新依赖）
- [x] key 纪律：`os.environ["CENSUS_API_KEY"]` 读取（与 census-eits 共用），`key=` 查询参数；缺失即 `RuntimeError`，不硬编码/不落文件/不静默降级；行内 `url` 已剔除 key（不随行数据落盘）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] `site` 未写（只访问 api.census.gov 公开 REST，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门
- [x] golden 样本 `golden/001-census-m3.json`（锚 MTM 2020-01 定稿历史值：VS 435587 / MI 244888 / NO 439561，min_rows 16）
- [x] 响应首字符必须 `[` 校验（无 key/超限的 200-HTML 错误页坑，brief.notes 落实）
- [x] 必填谓词 `category_code` + `seasonally_adj` 均随请求发送（实测缺任一 400）
- [x] **码位勘误留档**：brief 预设 334413（NAICS）实测 204——m3 只收助记码，落地 MTM 并在 README 记录换源路径
- [x] 本地自检：`health_verify.py --ticket reports/health-tickets/20261007-census-m3-a7211ee6.yaml` → `verdict: ok`
- [x] 仓级自检：`validate_manifests.py` / `check_manifest_commands.py` / `conformance_gate.py --roots .` 全绿
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由人工/CI 确认
- [ ] k8s envFrom 注入 `fd-industry-source-keys`（cronjob 模板已支持，见 95632df）——点亮前由人工确认
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）——**人工门，spider-heal 不点亮**
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身
