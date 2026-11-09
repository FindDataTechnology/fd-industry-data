# chinamoney-shibor 接入检查清单（批次六，2026-11-07）

- [x] 目录名 = manifest `name`（`chinamoney-shibor`；入口 `run_chinamoney_shibor`）
- [x] `spider.py` 提供 `run_chinamoney_shibor(limit=200) -> list[dict]`，纯标准库
- [x] **两个端点实读**：`cm-u-bk-shibor/ShiborHis`（八期限）+ `cm-u-bk-currency/FrrHis`（FR/FDR 六期限）
- [x] **ShiborHis 必须 POST**（GET 取不到 `records`），已复核成立
- [x] **行在顶层 `records`** 而非 `data.records`——本单元最贵的坑，已从 fragment JS 定位并写入 README
- [x] **假 200 嗅探**：非 JSON / 无顶层 `records` 判失败，退避 5s 重试一次，仍不过 `RuntimeError`
- [x] **窗口 ≤1 年**：从 `FIRST_DATE=2006-10-08` 按年分片（长区间实测返回空）
- [x] **间隔 3.5s**（站点限频严，侦察簿要求 ≥3–5s）
- [x] **单片失败留痕续跑**；全部分片 0 行 → `RuntimeError`（不静默回空）
- [x] **双锚返回口径**：最近 `limit` 行 + 2006-10-08 首日锚行（去重），README 写明为何这样设计
- [x] **首爬回补窗口 `window_start`/`window_end`**（平台属有参数，不上 manifest 声明面）：
      未带窗口=全史（行为不变）；带窗口只抓相交年分片（片长仍 ≤1 年，实测算 2 请求）；
      休市窗口回空不报假红；不成对/格式错/起晚于止 → `ValueError`
- [x] **`days` 默认收敛为增量**（`days=45`≈30 交易日 = 1 片 2 请求；`days=0`=全史）：
      `runner_cli` 不注入 manifest 默认值，故默认值必须自身便宜——否则日频每天重打
      同一指纹会触发 403 封禁；manifest 已声明 `days`，优先级 平台窗口 > `days`
- [x] 输出 6 列（curve/date/value/unit/source/scraped_at）与 manifest `columns` 一一对应；`limit` 声明可达
- [x] 缺测（`---`/null）不出行；`limit` 上限 `LIMIT_CAP=200_000`（全史回补）
- [x] local 自检：`validate_manifests` / `conformance_gate` 0 违规；golden schema+replay
      （`ok=True diffs=[]`，锚 `date=2006-10-08` / `curve=ShiborO/N` / `value=2.1184` 全中）
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮后试跑：验证 crawl_runs 记录与日频落库（首日锚值对标 README 首日实测）
