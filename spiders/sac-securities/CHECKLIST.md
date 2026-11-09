# sac-securities 接入检查清单（侦察骨架 → 正式单元落地）

- [x] 目录名 = manifest `name`（`sac-securities`；入口 `run_sac_securities`）
- [x] `spider.py` 提供 `run_sac_securities(limit=N) -> list[dict]`，只依赖标准库
      `urllib` + `re` + `html`（无新依赖）
- [x] **直连复测通过**：`sac.net.cn/xxgk/hysj/jjsj/` 列表页 + 文章页本机 curl 均
      HTTP 200（免 key、免鉴权），无需代理出口
- [x] manifest **不声明 `egress_secret`**、不硬编码代理地址（代码只访问固定 https 主机）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] **无 `schedule` 无 `site`**——静默合入，点亮留给人工门
- [x] 通道选择：官网无 JSON/表格/附件（逐一实测），唯一可行通道 = 文章正文散文
      解析（stdlib re）；`/hgsjbgz/` 失效、单位漂移、括号混用等坑位已记 README
- [x] 行键与 manifest columns 一致：source/stat_period/indicator/value/unit/
      company_scope/source_url/upstream_updated/scraped_at
- [x] golden 样本 `golden/001-sac-securities.json`（`limit=20` 重放）：**只锚结构
      常量**（`source` + `min_rows`），绝不锚任何当期金额；`scraped_at`/
      `upstream_updated` 进 whitelist
- [x] 口径文档化：`YYYY-Qn` 累计期号映射、万亿元→亿元归一、指标名剥离括号、
      公司家数不透出（README 码表）
- [x] 请求失败/列表无文章/单篇 0 指标行/总 0 行均抛 `RuntimeError`（失败即红）
- [x] 频次纪律：单次运行 = 列表页 1 次 + ≤8 篇文章页，不重试不批量
- [x] 侦察骨架 manifest（sac-securities/sac-securities-trading/securities）未改动，
      README 记录三者与本单元关系
- [x] 本地自检：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py` 全绿（不新增违规）
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮（人工补 `schedule`）后试跑：看 crawl_runs 记录与产出
