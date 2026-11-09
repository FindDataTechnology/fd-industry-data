# lhb-daily 接入检查清单（自建：沪深龙虎榜）

- [x] 目录名 = manifest `name`（`lhb-daily`；入口 `run_lhb_daily`）
- [x] `spider.py` 提供 `run_lhb_daily(limit=N) -> list[dict]`，只依赖标准库
      `urllib` + `json`（无新依赖；akshare 仅用于探测对照，不进 spider 依赖）
- [x] **直连复测通过**：`datacenter-web.eastmoney.com`（RPT_DAILYBILLBOARD_DETAILSNEW）
      本机 HTTP 200（免 key、免鉴权，2026-11-07 实测 79 行），无需代理出口
      （tencent 出口待 CI/人工补测）
- [x] manifest **不声明 `egress_secret`**、不硬编码代理地址（代码只访问东财官方主机）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] **无 `schedule`**——点亮由主代理统一排，manifest 留注释占位
- [x] 通道选择：**JSON**（东财 datacenter 聚合端点，akshare `stock_lhb_detail_em`
      同源且逐值一致）；席位明细 `RPT_BILLBOARD_DAILYDETAILSBUY` 实测可用，
      留作扩展不并入（README §1.2）
- [x] manifest `columns` 与输出键一一对应（source/symbol/name/trade_date/reason/
      close/change_pct/lhb_net_buy/lhb_buy/lhb_sell/source_url/scraped_at），
      parameters 仅 `limit`
- [x] concepts 三个：`market.lhb_net_buy`/`market.lhb_buy`/`market.lhb_sell`
      （entity_type: stock, frequency: daily, measure: amount, unit: currency_cny）
- [x] 单位口径：上游金额即元、涨跌幅即 %、收盘价即元——原值透传无换算
      （README §4）
- [x] 日期取最近有榜交易日：从今天（UTC+8）逐日回溯跳过无榜日（9201 空信封），
      上限 7 日；端点级故障不回溯掩盖
- [x] 缺列显式校验：身份列（SECURITY_CODE 非 6 位数字/name/reason）缺失即抛错
      并列出实得值；TRADE_DATE 与查询日期对账防串日；数值列缺失 → null（不编造）
- [x] 范围裁定：端点含北交所（MARKET=BJ），本单元沪深口径显式剔除（README §2）
- [x] 行粒度：每上榜股票×上榜原因一行（同股多原因金额各异，实测 688429），
      按（symbol, trade_date, reason）去重
- [x] golden 样本 `golden/001-lhb-daily.json`：**只锚结构常量**（`source` +
      `min_rows=3`），不锚 trade_date/具体代码/任何当期金额；`whitelist_fields:
      ["scraped_at", "upstream_updated"]`
- [x] 请求失败/非 JSON/信封异常/count 对账不平/身份列缺失/回溯 7 日无榜均抛
      `RuntimeError`（失败即红）
- [x] README 记端点/鉴权/通道选择/范围裁定/字段映射/实测证据/坑位
- [x] 本地自检：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py` / golden replay 全绿
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] tencent 站点出口直连复测（afdc 同款三出口证据表）
- [ ] 点亮（主代理统一补 `schedule`）后试跑：看 crawl_runs 记录与产出
