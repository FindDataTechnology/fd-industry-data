# nfra-penalty 接入检查清单（批次六，2026-11-07）

- [x] 目录名 = manifest `name`（`nfra-penalty`；入口 `run_nfra_penalty`）
- [x] `spider.py` 提供 `run_nfra_penalty(limit=N) -> list[dict]`，只依赖标准库 +
      `fd_industry_data.tabular`（docClob HTML 表走共享层 `read_grid`，无内联手写解析器）
- [x] **直连复测通过**：列表/详情/文档三端点本机可达（免 key 免鉴权）
- [x] manifest 不声明 `egress_secret`、不硬编码代理地址
- [x] manifest 字段齐全（version/name/label/source_url/schedule/data_period/functions/
      concepts/entities/fetch）；columns 13 键与输出行一一对应
- [x] 双头（`Referer` + `X-Requested-With`）恒带；`json.loads(strict=False)` 处理 docClob 控制字符
- [x] **`rptCode != 200` 退避 ≥90s**（实测另有裸 HTTP 403，同路径退避重试一次），
      超限则该叶/页放弃并留痕
- [x] `pageSize` 恒 20：翻页按 20 规划，不传自定义 pageSize 值
- [x] 只打三个叶子（4113/4114/4115）；931 聚合栏目（排序异常、total 不一致）与
      支局 4292（全局 2 条）不建通道
- [x] 服务端检索空操作 → 不做服务端过滤，按列表原样落库
- [x] **两种表头布局**（5 列 / 6 列含「行政处罚决定书文号」）按表头别名映射，
      不按列序号写死
- [x] 行粒度 = 表行（同一 docId 可多行）；`penalty_no` 列缺席时回落标题括注
- [x] 金额口径：该行罚款合计（万元），没收违法所得不计；无罚款 → `null`（不填 0）
- [x] **叶子轮转**：先取三叶列表再按文档轮转，避免总局批量列表吃满 `limit`
      （首爬 limit=60 全是 general_bureau 的缺陷已修）
- [x] **PDF 不解析**：36/36 篇 docClob 均有 HTML 表；PDF 为同表 WPS 渲染件，
      `-layout` 列塌陷、`-bbox-layout` 5 列并 4 列且 1277175 SIGABRT —— 证据与理由入 README
- [x] golden `golden/001-nfra-penalty.json`：三叶/三层结构锚 + 真值锚
      （兴业 695 万 / 光大 180 万 / 甘金监行处〔2026〕433号 / 河南金融监管局）
- [x] 失败策略：单文档跳过留痕、单叶失败不阻塞、全失败 `RuntimeError`、`limit` 生效
- [x] README 记端点/双头/限流假 200/三叶口径/两种表头/金额口径/轮转/PDF 不解析证据/实测证据/坑位
- [x] 本地自检：`validate_manifests` / `check_manifest_commands` / `conformance_gate` /
      golden schema+replay 全绿（首爬 150 行 / 57 篇实测）
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮后试跑：验证 crawl_runs 记录与日频发布窗触发（当日发布次日常规可取）
