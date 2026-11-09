# yindeng-npl-transfer 接入检查清单（批次六，2026-11-08）

- [x] 目录名 = manifest `name`（`yindeng-npl-transfer`；入口 `run_yindeng_npl_transfer`）
- [x] `spider.py` 提供 `run_yindeng_npl_transfer(limit=, kinds=, start_page=) -> list[dict]`，纯标准库
- [x] **两路列表实读**：挂牌 `bldkzr_xxpl_zrgg`（616 页 ≈6,155 条）+ 成交 `bldkzr_xxpl_zrjggg`（303 页 ≈3,030 条）
- [x] **CMS 静态分页**：页脚 `createPageHTMLApp(total, idx, "index", "html")` + `index_<N>.html`，每页 10 条
- [x] **页号方向留痕**：`index_K` = 自旧往新第 K 页（`index_1` 2021 最旧 / 最高号 2026 最新），`index.html` = 更新的当前页
- [x] **详情页 → PDF 直链**：`…/YYYYMM/tYYYYMMDD_<id>.html` → `…/YYYYMM/P0202….pdf`（`%PDF-1.4`，无鉴权）
- [x] **PDF 文本层走共享层**：`fd_industry_data.pdftext.extract_text(payload, layout=True)`，不手写子进程
- [x] **竖排水印剔除**：纯水印 token（≤4 字、字符集 `未经许可不得转载`）逐 token 丢弃 + 空行丢弃；粘连前缀按标签 `search` 容忍；实测 184→76 行 / 5,189→1,440 字
- [x] **字段抽取**：`项目编号`（NPL*）/`出让方全称`/`受让方全称`/`转让协议签署日期`/`交易基准日`/`资产笔数（笔）`/`借款人户数（户）`/`加权平均逾期天数`/未偿本息三项/`五级分类情况`/`转让方式`
- [x] **扫描件不伪造**：2021/2022 部分 PDF 无文本层（实测 pdftotext 仅 4–6 字）→ 只出行列表元数据，数值 `null`、`text_layer=false`、留痕
- [x] **发布时间口径**：取详情页 `发布时间` 字段（2024-12 改版后 `/YYYYMM/` 目录日期失真，实测 `202412/` 下为 `2024-09-04`）
- [x] `start_page` 参数定页（0=最新，N=静态页 index_N 向新翻），golden 钉死冻结页（增量参数，README 留痕理由）
- [x] `kinds` 参数真实生效（`zrgg`/`zrjggg`/空=两路；非法值 `ValueError`）；`limit` = 每路上限
- [x] manifest 字段齐全；columns 21 键与输出行一一对应；三个声明参数均可达
- [x] 失败策略：单条详情/PDF 失败保留元数据续跑、两路全空 `RuntimeError`、静态页缺失停止向旧翻
- [x] 网络礼节：请求间隔 ≥1s（`_fetch` 统一节流）
- [x] 本地自检（2026-11-08，`.venv/bin/python`，系统 `python3` 缺 pyyaml）：
      `validate_manifests` 146 manifests 0 violations / `conformance_gate`
      PASS 148 units 0 violations / golden `schema [] ok=True rows=4 diffs=[]`
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮后试跑：验证 crawl_runs 记录与公告落库（首爬对标 README 4 行/13.6s，默认 100 条/路 ≈7 分钟）
