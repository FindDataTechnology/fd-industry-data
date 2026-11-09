# sse-szse-market-stats 接入检查清单（自建：沪深市场统计）

- [x] 目录名 = manifest `name`（`sse-szse-market-stats`；入口 `run_sse_szse_market_stats`）
- [x] `spider.py` 提供 `run_sse_szse_market_stats(limit=N) -> list[dict]`，只依赖标准库
      `urllib` + `json`（无新依赖）
- [x] **直连复测通过**：`query.sse.com.cn`（须 Referer）+ `www.szse.cn/api/report/ShowReport/data`
      本机均 HTTP 200（免 key、免鉴权），无需代理出口（tencent 出口待 CI/人工补测）
- [x] manifest **不声明 `egress_secret`**、不硬编码代理地址（代码只访问两所官方主机）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] **无 `schedule` 无 `site`**——静默合入，点亮留给人工门
- [x] 通道选择：**JSON**（SSE commonQuery + SZSE ShowReport/data）；brief 原文的
      SZSE `1803_scxw`（noalert 报错）与 SSE `DAYMD_C`（result:null）为坏通道，
      真通道及发现过程已记 README
- [x] manifest `columns` 与输出键一一对应（source/exchange/trade_date/turnover_amount/
      turnover_volume/avg_pe/source_url/scraped_at），parameters 仅 `limit`
- [x] 单位统一：亿元×1e8→元、亿股×1e8→股、市盈率(倍)透传（README §4 对照表）
- [x] 缺列显式校验：SSE 核心列（TRADE_DATE/TRADE_AMT/TRADE_VOL）与 SZSE 中文指标
      名漂移即抛错并列出实际键/指标；`avg_pe` 缺失 → null（不编造）
- [x] golden 样本 `golden/001-sse-szse-market-stats.json`：**只锚结构常量**
      （`source`/`exchange` + `min_rows=2`），不锚 trade_date 与任何当期量额/PE 值；
      `whitelist_fields: ["scraped_at", "upstream_updated"]`
- [x] 请求失败/非 JSON/报表 error/缺结构/中文列名漂移/回溯 10 日 0 观测均抛
      `RuntimeError`（失败即红）
- [x] README 记端点/鉴权/通道选择/单位换算/实测证据/坑位
- [x] 本地自检：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py` / golden replay 全绿
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] tencent 站点出口直连复测（afdc 同款三出口证据表）
- [ ] 点亮（人工补 `schedule`）后试跑：看 crawl_runs 记录与产出
