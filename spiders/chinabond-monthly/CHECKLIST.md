# chinabond-monthly 接入检查清单（批次六，2026-11-08）

- [x] 目录名 = manifest `name`（`chinabond-monthly`；入口 `run_chinabond_monthly`）
- [x] `spider.py` 提供 `run_chinabond_monthly(limit=, months=, period=) -> list[dict]`，纯标准库
- [x] **两步端点实读**：`GetMonthReport/queryContent`（`reportFlag=T`）+ `GetMonthReport/GetDataByte`（`sWjlx=3`）
- [x] **`reportFlag` 坑留痕**：`T` 才出 8 表，`0/1/2` 一律 `[]`（README 硬坑 1）
- [x] **未发布月判定**：`[]` ≠ 错误，当"未发布"往回探测；2026-10 未发 / 2026-09 已发实测
- [x] **表号枚举校验**：`bbbh` 限于 `01..08`；越界留痕丢弃；表名漂移留痕以表号为准
- [x] **8 表全覆盖解析**（含 01 表无「合计」行、07/08 两行表头、06 六券种列）
- [x] **单位显式规则表**（01 按行 / 04 按列），不做"token 数==列数"的错误对齐
- [x] **跨表自洽对账**：三期 01/04/05 托管量 + 01/02 发行量逐月相等（README 表）
- [x] `period` 参数钉死 golden 月份（增量参数，README 留痕理由与对 spec 的关系）
- [x] manifest 字段齐全；columns 10 键与输出行一一对应；三个声明参数均可达
- [x] xlsx 走共享层 `tabular`（内联字符串形态，内联解析会被 gate 拦）
- [x] 失败策略：单表失败留痕续跑、探测窗全空 `RuntimeError`、缺测不出行、
      `limit`/`months`/`period` 均真实生效
- [x] 本地自检：`validate_manifests`（136 单元 0 违规）/ `conformance_gate`
      （138 单元 0 违规）/ golden schema+replay（`ok=True rows=284 diffs=[]`）
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮后试跑：验证 crawl_runs 记录与月报落库（首月对标 README 284 行/期）
