# cba-wealth-index 接入检查清单（批次六，2026-11-08）

- [x] 目录名 = manifest `name`（`cba-wealth-index`；入口 `run_cba_wealth_index`）
- [x] `spider.py` 提供 `run_cba_wealth_index(limit=, months=, period=) -> list[dict]`，纯标准库（docx 用 `zipfile`+`xml.etree`）
- [x] **年度聚合页实读**：栏目页 `catid/279` → 4 个 `catid/280` 详情页（2026/2025/2024/2022-2023），共 53 期
- [x] **批次目录陷阱留痕**：`/Uploads/ueditor/file/<YYYYMMDD>/` 是上传批次日期；2026 页 `20261012/` 装 1–8 月、`20261019/` 装 9 月——期号只从锚文本取（README 三种错法 + 证据表）
- [x] **同期重复挂链去重**：2023-01 两次挂链（文件 sha256 相同），按 `period` 去重、取批次日期最新
- [x] **期号交叉校验**：文档标题 `（YYYY 年 M 月）` 与锚文本期号不一致时留痕
- [x] **PDF 分支走共享层**：`fd_industry_data.pdftext.extract_text(payload, layout=True)`，不手写 PDF 解析
- [x] **PDF 有文本层实测**：2026-09 原文可抽（README 引原文），非扫描件、不需 OCR
- [x] **docx 分支保留且实测存活**：`word/document.xml` + `w:p`/`w:t`；`period=2023-12, months=2` 真跑 2 期各 13 行
- [x] **口径断层分列**：`methodology=fixed_income_return_index`（2024-01+）/ `selected60_price_index`（2022-02…2023-12），不拼成同一序列
- [x] **正则覆盖跨期措辞**：`为+`（「为为」）、`点` 可缺、（点|%）单位歧义、六个月省略主语（先三个月再同句后段）
- [x] `period` 参数钉死 golden 月份（增量参数，README 留痕理由）
- [x] **三参数实跑验证**：`period=2024-01, months=2` 得跨年两期 38 行（新 25 + 旧 13）；
      同参数 `limit=10` 返回 10 行——`limit/months/period` 均真实生效
- [x] manifest 字段齐全；columns 10 键与输出行一一对应；三个声明参数均可达
- [x] 请求间隔 ≥1s（`_throttle`），瞬时错误重试一次
- [x] 失败策略：年度页/单期失败留痕续跑、全空 `RuntimeError`、无文本层不回填假值、
      `limit`/`months`/`period` 均真实生效
- [x] 本地自检：`validate_manifests`（145 manifests 0 违规）/ golden schema+replay
      （`ok=True rows=32 diffs=[]`）
- [ ] `conformance_gate` 全仓 0 违规——本单元首跑时并行兄弟单元 `spiders/ratings-ccxi/`
      仅有 `spider.py` 尚无 manifest（非本单元），待其落盘后复跑确认
- [ ] 点亮后试跑：验证 crawl_runs 记录与月报落库（首月对标 README 32 行/期）
