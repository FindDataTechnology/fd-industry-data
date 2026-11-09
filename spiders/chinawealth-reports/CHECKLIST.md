# chinawealth-reports 接入检查清单（批次六，2026-11-08）

- [x] 目录名 = manifest `name`（`chinawealth-reports`；入口 `run_chinawealth_reports`）
- [x] `spider.py` 提供 `run_chinawealth_reports(limit=, pages=) -> list[dict]`，纯标准库
- [x] **列表分页实读**：首页 `index.html` + `let pageCount = Number('3')`；
      第 k 页 = `index_{pageCount-k+1}.html`（实测 1/`index_2`/`index_1`，`index_3` = 404）
- [x] **详情页 `data-recurl` 实读**：空 `<li class='file-list' data-recurl='./P0….pdf'>`
      + 页面 JS 拼链；静态 HTML 无 `<a href>`，值按详情页目录 urljoin 成绝对地址
- [x] **全部 28 条详情页实测**：27 条有 PDF 直链，1 条纯文字合并公告无附件（留痕，
      仍出行、`pdf_url=null`）
- [x] **PDF 可达性抽检**：最新期 200 / `application/pdf` / `%PDF-1.7` / 763,528 B
      （仅验证直链，不下载正文）
- [x] 请求头实测：Chrome UA + `Referer` 栏目页，31 次请求全 200；无 403/302/验证页；
      `/robots.txt` = 404（无声明）；间隔 1.0s
- [x] `pages` 参数真实生效：控制列表翻页深度（1..pageCount），不硬编码页码后缀
- [x] `limit` 参数真实生效：排序后截断（默认 60 > 全量 28）
- [x] manifest 字段齐全：v2 / data_period month / schedule `44 15 * * 2` /
      两参数均可达 / 10 列与输出行一一对应 / concepts / entities /
      `fetch.module: fd_industry_data.provider:IndustryDataProvider`
- [x] 元数据从 HTML 字符串切片抽取（列表无 `<table>`，不自造表格解析器）；
      本单元不解析 PDF 正文，故不涉及 `fd_industry_data.pdftext`
- [x] 失败策略：首页无条目 `RuntimeError`、翻页失败停页留痕、单详情失败回退列表元数据、
      `limit/pages<=0` 返回 `[]`
- [x] golden 锚稳定等值字段：`issue`/`title`/`published_at`（2026年三季度快照）+
      `pdf_url` + `doc_type` 三值 + `publisher` + 两处边界形态；理由见 README §5
- [x] 本地自检：`validate_manifests` / `conformance_gate`（0 violations）/
      golden schema + replay（`ok=True rows=28 diffs=[]`）
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮后试跑：验证 crawl_runs 记录与 28 条元数据落库
