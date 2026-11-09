# sse-szse-margin 接入检查清单（自建单元，2026-11-07）

- [x] 目录名 = manifest `name`（`sse-szse-margin`；入口 `run_sse_szse_margin`）
- [x] `spider.py` 提供 `run_sse_szse_margin(limit=N) -> list[dict]`，只依赖标准库
      `urllib` + `json`（无新依赖）
- [x] **直连复测通过**：SSE `query.sse.com.cn/marketdata/tradedata/queryMargin.do`
      与 SZSE `www.szse.cn/api/report/ShowReport/data?CATALOGID=1837_xxpl` 本机
      均 HTTP 200（免 key、免鉴权），无需代理出口（见 README 证据表）
- [x] manifest **不声明 `egress_secret`**、不硬编码代理地址（代码只访问固定 https 官方主机）
- [x] manifest 字段齐全：version/name/label/source_url/functions（command/frequency/
      parameters/columns）/concepts/entities/fetch；columns 与输出行 8 键一一对应
- [x] **无 `schedule` 无 `site`**——静默合入，点亮留给人工门
- [x] 通道选择：两所**官方 JSON**（SSE queryMargin.do 一次窗口取数；SZSE
      ShowReport 1837_xxpl 逐日回溯）；brief 起点 sqlId 空壳、SZSE 2003_sczm
      失效等坑位已实测并记 README
- [x] 单位统一为**元**：SSE 原生元；SZSE 列名标注亿元→×1e8，换算系数从列名
      动态解析，缺标注即抛错（不猜口径）
- [x] golden 样本 `golden/001-sse-szse-margin.json`（`limit=10` 重放）：**只锚
      结构常量**（`source` + `exchange=SZSE` + `min_rows=2`），不锚任何当期
      余额数值/日期；`scraped_at`/`upstream_updated` 进 whitelist
- [x] 请求失败/非 JSON/缺结构/缺列（含列名清单）/0 观测行均抛 `RuntimeError`
      （失败即红，绝不静默回空）；单所失败另一所照常产出（README 论证）
- [x] README 记端点/鉴权头/单位换算/实测证据（HTTP 状态/行数）/坑位
- [x] 本地自检：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py`（不新增违规）/ golden validate+replay 全绿
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮（人工补 `schedule`）后试跑：看 crawl_runs 记录与产出
      （注意 SZSE 逐日回溯请求量：`limit=50` 约 170 次请求）
