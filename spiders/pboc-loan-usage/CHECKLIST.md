# pboc-loan-usage 接入检查清单（批次六，2026-11-07）

- [x] 目录名 = manifest `name`（`pboc-loan-usage`；入口 `run_pboc_loan_usage`）
- [x] `spider.py` 提供 `run_pboc_loan_usage(limit=N) -> list[dict]`，只依赖标准库
      `urllib/json/re/datetime`（无新依赖）
- [x] **直连复测通过**：`www.pbc.gov.cn/goutongjiaoliu/113456/113469/` 本机 HTTP 200
      （免 key、免鉴权），无需代理出口
- [x] manifest 不声明 `egress_secret`、不硬编码代理地址
- [x] manifest 字段齐全：version/name/label/source_url/schedule/data_period/functions
      （command/frequency/parameters/columns）/concepts/entities/fetch；columns 9 键与
      输出行一一对应
- [x] 通道：目录页 + `11040-<n>.html` 翻页（末页号页内读出，不硬编码）
- [x] 坑位落实：`<sup>` 脚注先删再剥标签；发布日期取 meta PubDate（不解析 URL）；
      混合新闻流按标题匹配；标题口径漂移按期次去重
- [x] 增速带符号：`下降` 记负、`持平` 记 0（房地产/开发贷当前为负）
- [x] golden 样本 `golden/001-pboc-loan-usage.json`：锚 2026Q3 真值三组
      （283.44 / 38.5 / 54.29）+ 结构常量；`scraped_at` 进 whitelist
- [x] 失败策略：单篇跳过留痕、全失败 `RuntimeError`、抽不到不出行、limit 生效
- [x] README 记端点/鉴权/抽取口径/实测证据/坑位/`data_period=irregular` 理由
- [x] 本地自检：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py` / golden schema+replay 全绿（首爬 24 行）
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮后试跑：看 crawl_runs 记录与季度窗触发是否符合预期
