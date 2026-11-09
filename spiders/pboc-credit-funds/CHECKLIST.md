# pboc-credit-funds 接入检查清单（批次六，2026-11-07）

- [x] 目录名 = manifest `name`（`pboc-credit-funds`；入口 `run_pboc_credit_funds`）
- [x] `spider.py` 提供 `run_pboc_credit_funds(limit=N) -> list[dict]`，只依赖标准库 +
      `fd_industry_data.tabular`（xlsx 走共享层 `read_grid`，无内联手写解析器）
- [x] **直连复测通过**：索引/栏目/九表 xlsx 全端点本机 200（免 key 免鉴权）
- [x] manifest 不声明 `egress_secret`、不硬编码代理地址
- [x] manifest 字段齐全；columns 13 键与输出行一一对应
- [x] **月份截零坑**：`2026.1` = 10 月，`_parse_month()` 按小数位宽度还原；
      实测 9 月值与原 sheet 单元格逐字符一致
- [x] `sharedStrings` `\xa0` / `\u3000` 中英双行 → 指标名取第一个含中文段
- [x] 表头按内容定位（`项目` + 月份标签），不写死行号；首 5 行噪音不参与定位
- [x] `注：…` 脚注段由「无有效值不产行」自然跳过
- [x] **年度栏目动态定位**（索引内链取最新年度），跨年不改代码
- [x] 九张表按标题精确匹配（9/9 命中）；标题不认识 → 留痕跳过，不猜
- [x] 行粒度 =（表, 指标, 月份）；`side` 由 `来源方项目`/`运用方项目` 段落派生；
      `indicator_level` 由序号形态派生
- [x] **原位替换写清边界**：栏目页只有最新一期；逐月序列靠平台按月累积，
      历史回补是离线动作，不进排期路径
- [x] 九表**轮流取行**的截断策略（单表不会吃满 `limit`）
- [x] golden `golden/001-pboc-credit-funds.json`：9 表/3 币种/2 侧结构锚 +
      真值锚（各项存款 3,575,240.64 / 债券投资 947,433.92 / 月份 2026-09 / 单位亿元）
- [x] **表内平衡自检**：来源总计 == 运用总计（四表全平，证据入 README）
- [x] 失败策略：单表跳过留痕、九表全空 `RuntimeError`、空格不出行、`limit` 生效
- [x] README 记端点/九表/截零坑/指标名口径/原位替换边界/实测证据/坑位
- [x] 本地自检：`validate_manifests` / `check_manifest_commands` / `conformance_gate` /
      golden schema+replay 全绿（首爬 2,997 行 / 9 表实测）
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮后试跑：验证 crawl_runs 记录与月中发布窗触发（实测 10-14 发 9 月表）
