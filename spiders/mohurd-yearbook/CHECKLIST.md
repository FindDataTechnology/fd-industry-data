# mohurd-yearbook 接入检查清单（change tabular-extraction-layer）

- [x] 目录名 = manifest `name`（`mohurd-yearbook`；入口 `run_mohurd_yearbook`）
- [x] `spider.py` 提供 `run_mohurd_yearbook(limit, dataset) -> list[dict]`
      ——**零手写解析**，全部经平台共享层 `fd_industry_data.tabular`
      （`detect` / `sheets` / `read_grid` / `unzip` / `strip_latin_runs` / `squash_cjk`）
- [x] manifest 字段齐全：version/name/label/source_url/schedule/scanner_mode/ranking_seed/functions/columns/concepts/entities/fetch
- [x] manifest `columns` 与 spider 实际产出字段逐一对应（11 列，已核对）
- [x] `site` 未写（只访问 www.mohurd.gov.cn 公开附件，走缺省执行位 tencent）
- [x] 排期 `52 6 12 1 *`（年刊 → 年频，符合「调度节奏与声明频度一致」规格）
- [x] golden 样本 `golden/001-mohurd-yearbook.json`：锚**历史年份**（1981/1990）
      与不可变表名/指标名，不锚当年值（年刊下年必变会成假故障）；两数据集均须出现
- [x] 坑位已落实并写进 README：表头高度不固定 / 中英同格需剥离 / 中文对齐空格 /
      `limit` 被单数据集吃光（改轮转交织）/ 非年份行跳过 / 数值保留字符串
- [x] 容错已落实：单表/单文件读取失败跳过留痕、附件缺失留痕不静默、
      空单元格不产行（不填 0、不插值）
- [x] 真实取数证据已入 README（含 ZIP 中文名还原前后对照 + 4 条样例行）
- [x] 本地自检：`python3 -m py_compile spiders/mohurd-yearbook/spider.py` 通过
- [x] 仓级自检：`validate_manifests.py` / `conformance_gate.py --roots .` 全绿
- [ ] 镜像重建（依赖新增 `xlrd`）后 `import_scan` 通过
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 首爬：触发后核对 crawl_runs 记录与行数
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身
