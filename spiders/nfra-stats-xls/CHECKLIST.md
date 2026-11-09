# nfra-stats-xls 接入检查清单（批次六，2026-11-07）

- [x] 目录名 = manifest `name`（`nfra-stats-xls`；入口 `run_nfra_stats_xls`）
- [x] `spider.py` 提供 `run_nfra_stats_xls(limit=N) -> list[dict]`，只依赖标准库 +
      `fd_industry_data.tabular`（BIFF8 走共享层，无内联 workbook 解析）
- [x] **直连复测通过**：列表/详情/附件三端点本机 HTTP 200（免 key 免鉴权）
- [x] manifest 不声明 `egress_secret`、不硬编码代理地址
- [x] manifest 字段齐全（version/name/label/source_url/schedule/data_period/functions/
      concepts/entities/fetch）；columns 12 键与输出行一一对应
- [x] **裁决前提核验**：附件通道无需 OCR（正文全 PNG、PDF 无文本层已复测），
      不触及 2026-10-13「需 OCR 才接」裁决原文；README 记录裁决保留效力
- [x] 双头（Referer + X-Requested-With）恒带；`json.loads(strict=False)` 处理 docClob 控制字符
- [x] **原位更新专条**：月度表每次重查 `SelectByDocId` 取最新附件直链，**不缓存 uuid**
- [x] 标题口径漂移按关键词组匹配（「（季度）」后缀差异不影响）
- [x] 五族表全覆盖：主要监管指标 / 分机构类（交叉表）/ 总资产负债（月+季）/
      普惠小微 / 普惠涉农；两种表头布局（周期在项目行 / 在时间行）均兼容
- [x] 比率归一：Excel 小数形 ×100 → 百分数（不良率 0.01518→1.518），README 记判定依据
- [x] 表族轮转排序：避免月度表 360 行饿死其它四族（首爬 limit=60 全 balance_sheet 的缺陷已修）
- [x] golden `golden/001-nfra-stats-xls.json`：五族结构锚 + 真值锚
      （不良率 1.518 / 资本充足率 15.256 / 不良余额 37248.816429047）+ 百分比单位
- [x] 失败策略：单文档跳过留痕、全失败 `RuntimeError`、空格不出行、limit 生效
- [x] README 记端点/双头/五族表/两种表头/比率口径/原位更新/实测证据/坑位
- [x] 本地自检：`validate_manifests` / `check_manifest_commands` / `conformance_gate` /
      golden schema+replay 全绿（首爬 800 行、三年 1,600 行实测）
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮后试跑：验证 crawl_runs 记录与发布窗触发（季度表 2/8 月、月度表月中下旬）
