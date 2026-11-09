# chinabond-yield-curve 接入检查清单（批次六，2026-11-08）

- [x] 目录名 = manifest `name`（`chinabond-yield-curve`；入口 `run_chinabond_yield_curve`）
- [x] `spider.py` 提供 `run_chinabond_yield_curve(limit=1200, days=0) -> list[dict]`，纯标准库 + 共享层
- [x] **两端点实读**：`queryTree`（曲线码表 180 节点）+ `downYearBzqx`（整年包）
- [x] **Referer 必带**（无 Referer 实测被拒），已固定进 `HEADERS`
- [x] **响应是 xlsx 而非 msdownload**：`sharedStrings` 空、字符串内联在 sheet XML，
      故走共享层 `tabular.read_grid`（内联解析会被 `inline-tabular-parser` 拦）
- [x] **表头探测**：前 5 行内找含「日期」+「收益率」的行，找不到即该曲线失败，不猜列序
- [x] **7 条曲线码表实读硬编码**（二级资本债 AAA-/AA＋/AA/AA-/A＋/A + TLAC 非资本债 AAA-），
      曲线名保留上游原始写法（含全角 `＋`）
- [x] 侦察簿路 5 两坑留痕并绕开：`GetDataByte` 的 `sCode` 差一位、`searchYc` 已死——本单元不碰
- [x] **死常量已清**：早前 `SLEEP = 1.5` 从未被使用（会误导读者以为有节流），已删并注明
      "7 个请求实测稳定、不设间隔"
- [x] 单曲线失败留痕续跑；7 条全空 `RuntimeError`；缺测不出行不补 0
- [x] 输出 9 列（date/curve_id/curve_name/tenor_label/tenor_years/yield_pct/unit/source/scraped_at）
      与 manifest `columns` 一一对应；`limit`/`days` 声明参数均可达
- [x] local 自检：`validate_manifests`（155 manifests 0 违规）/ `conformance_gate`
      （本批 20 单元 0 违规）/ golden schema+replay（`ok=True rows=218 diffs=[]`，`days=3`）
- [x] 五件套齐备（manifest + spider + golden + README + CHECKLIST）
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮后试跑：验证 crawl_runs 记录与日频曲线点落库（对标 README 218 行/3 日窗）
