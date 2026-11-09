# chinamoney-ncd-curve 接入检查清单（批次六，2026-11-07）

- [x] 目录名 = manifest `name`（`chinamoney-ncd-curve`；入口 `run_chinamoney_ncd_curve`）
- [x] `spider.py` 提供 `run_chinamoney_ncd_curve(limit=N, days=N) -> list[dict]`，纯标准库
- [x] **端点实读**：fragment `bond/cls-yld-curv-cn.html` 的 JS 给出
      `cm-u-bk-currency/ClsYldCurvHis?lang=CN&reference=&bondType=&startDate=&endDate=&termId=0.5`
- [x] **曲线码实读**：`ClsYldCurvCurvGO` 码表（75 条），CYCC41B = 同业存单(AAA)；
      AA+/AA/AAA-/AA-/A+/A/A- 各码一并登记 README
- [x] **单日查坑**：区间（8-01..11-07）实测 0 条、单日实测 17 条 → 按交易日逐日走，README 记明
- [x] **行在顶层 `records`**（`data` 只放元信息），已实测
- [x] `reference=1`（到期）→ 取 `maturityYieldStr`；即期/远期不请求
- [x] 期限结构实测 17 点（0.019…1.0 年），`tenor_years` 原始 + `tenor_days` 派生
- [x] **即接即累积纪律落地**：只走 `days` 回溯窗、连续 3 空提前收工、不回补历史；
      caveat（线上仅滚动约 2 月、中断即缺段、gap_in_series 兜底）写入 README + manifest
- [x] 假 200 嗅探（非 JSON / 无顶层 records → 退避 60s 重试一次）；端点间隔 3.5s
- [x] manifest 字段齐全；columns 10 键与输出行一一对应；两个声明参数均可达
- [x] **golden 口径偏离已留痕**：3.3 原文「锚点亮日最新值」改为**结构锚**
      （期限集合 + 曲线码 + reference + unit + min_rows），理由与点亮日全期限快照
      双写入 README（每日变动数值锚会让 golden 次日即红）
- [x] 失败策略：单日失败留痕续跑、整窗无点 `RuntimeError`、缺测不出行、`limit`/`days` 生效
- [x] 本地自检：`validate_manifests` / `conformance_gate` / golden schema+replay
      （首爬 119 行 / 7 交易日 / 17 期限实测）
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮后试跑：验证 crawl_runs 记录与日频曲线落库（首日落库值对标 README 快照表）
