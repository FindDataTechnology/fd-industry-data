# chinamoney-lpr 接入检查清单（批次六，2026-11-07）

- [x] 目录名 = manifest `name`（`chinamoney-lpr`；入口 `run_chinamoney_lpr`）
- [x] `spider.py` 提供 `run_chinamoney_lpr(limit=N) -> list[dict]`，纯标准库（JSON 通道无需表格层）
- [x] **端点实读**（非猜测）：fragment `currency/lpr-his-cn.html` 的 JS 给出
      `cm-u-bk-currency/LprHis?lang=CN&strStartDate=&strEndDate=` + POST
- [x] **参数名独有**：`strStartDate`/`strEndDate`（不是 startDate/endDate），README 记明
- [x] **行在顶层 `records`**：`data.records` 恒空已实测两次，README 记明该坑
- [x] **窗口 ≤1 年**：>1 年静默返空已实测（2.8 年窗 0 条），故按年分片（2013 起）
- [x] **假 200 嗅探**：非 JSON 或无顶层 `records` → 退避 60s 重试一次 → 仍不过放弃该片留痕
- [x] 端点间隔 3.5s（同站 ≥3-5s 纪律）
- [x] manifest 字段齐全；columns 6 键与输出行一一对应；不声明 egress_secret、无硬编码代理
- [x] 行粒度 =（报价期次, 期限）：`LPR1Y` / `LPR5Y`，单位 %
- [x] 空值/`---` 不出行（不填 0）；`limit` 真实生效、newest-first
- [x] 失败策略：单片失败留痕续跑、全空 `RuntimeError`
- [x] golden `golden/001-chinamoney-lpr.json`：两期限 + 真值锚（2026-10-20 / 3.0 / 3.5）+ 单位
- [x] README 记端点/参数名坑/顶层 records 坑/窗口坑/假 200/实测证据/失败策略
- [x] 本地自检：`validate_manifests` / `conformance_gate` / golden schema+replay
      全绿（首爬 80 行 / 40 期次实测）
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮后试跑：验证 crawl_runs 记录与 20 日报价窗触发
