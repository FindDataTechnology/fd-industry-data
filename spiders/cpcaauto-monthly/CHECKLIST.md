# cpcaauto-monthly 接入检查清单（industry-source-expansion 批次五）

- [x] 目录名 = manifest `name`（`cpcaauto-monthly`；入口 `run_cpcaauto_monthly`）
- [x] `spider.py` 提供 `run_cpcaauto_monthly(limit, max_pages, category) -> list[dict]`
      ——只依赖标准库 + scrapling，**无 PDF/Excel 库、无新增三方依赖**
- [x] manifest 字段齐全：version/name/label/source_url/schedule/scanner_mode/ranking_seed/functions/columns/concepts/entities/fetch
- [x] manifest `columns` 与 spider 实际产出字段逐一对应（9 列，已核对）
- [x] `site` 未写（只访问 www.cpcaauto.com 公开页面，走缺省执行位 tencent）
- [x] 排期 `31 6 * * *`（点亮批次五；vs 全仓既有排期做分钟级交集校验）
- [x] golden 样本 `golden/001-cpcaauto-monthly.json`：
      锚历史报告 id（4350/4349/4348）+ 不可变站点标识符 `article_id` + 类别覆盖；
      实测重放 diffs 空
- [x] 坑位已落实并写进 README：列表必走 news.php（newslist.php 无 id 返回 MySQL 报错壳 200）
      / 必须 impersonate='chrome'（裸 UA 33 字节拦截页）/ 标题前导 &nbsp; / 正文叙述文不抽数值
      / 附件 .rar 只溯源 / period 兜底规则
- [x] 容错已落实：单次尝试不重试、单 URL 失败跳过、正文 <80 字跳过、
      去重键 `article_id`、`limit` 真实生效、`page` 上限受 max_pages 约束
- [x] `attach_url` 仅作溯源列，代码中从未下载、从未作为解析输入
- [x] 真实取数证据已入 README（4 条实测 + 正文数值样本 161.8 万辆 / 67.0% / 91.7 万辆）
- [x] 本地自检：`python3 -m py_compile spiders/cpcaauto-monthly/spider.py` 通过
- [x] 仓级自检：`validate_manifests.py` / `conformance_gate.py --roots .` 全绿
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 首爬：触发后核对 crawl_runs 记录与行数
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身

## 首爬后补记（2026-10-13，实测修正）

- [x] 首爬两轮均 success rows=31（3 页 60 条报告 → 31 行）
- [x] **归因修正**：首轮以为差额是"无间隔连发被节流"，复验**推翻**——差额真因是
      **8/20 报告为图文报告**（`div.text` 内只有 `<img>`、文本 0 字），属用户
      2026-10-13 裁决的 OCR 类，按规不处理。节流（0.4s）保留为仓内常规礼貌间隔。
- [x] 代码据此加 `img_count` 统计，跳过日志区分 `imgs>0`（OCR 类）/ `imgs==0`
      （页面异常，值得复查），README 与模块 docstring 同步更正
