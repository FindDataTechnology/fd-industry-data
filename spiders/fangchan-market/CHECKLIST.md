# fangchan-market 接入检查清单（industry-source-expansion 批次五）

- [x] 目录名 = manifest `name`（`fangchan-market`；入口 `run_fangchan_market`）
- [x] `spider.py` 提供 `run_fangchan_market(limit, max_pages, subcolumn, page_size) -> list[dict]`
      ——只依赖标准库 + scrapling，**无 PDF/Excel 库、无新增三方依赖**
- [x] manifest 字段齐全：version/name/label/source_url/schedule/scanner_mode/ranking_seed/functions/columns/concepts/entities/fetch
- [x] manifest `columns` 与 spider 实际产出字段逐一对应（9 列，已核对）
- [x] `site` 未写（只访问 news.fangchan.com 与 www.fangchan.com 公开接口，走缺省执行位 tencent）
- [x] 排期 `41 6 */2 * *`（点亮批次五；vs 全仓既有排期做分钟级交集校验）
- [x] golden 样本 `golden/001-fangchan-market.json`：
      锚历史文章 id（7515241843282744309 / 7514495726509167279）+ 不可变站点标识符 `article_id`；
      实测重放 diffs 空
- [x] 坑位已落实并写进 README：频道页主列表 JS 空壳（必须走 API）/ 五种分页路径全 404
      / 正文剥分享与二维码噪音 / createtime 是 unix 秒按 UTC+8 落日期 / API 与站点不同域
      / 正文叙述文不抽数值 / pic_url 只溯源
- [x] 容错已落实：单次尝试不重试、单 URL 失败跳过、正文 <120 字跳过、
      API 载荷非法上抛不吞、API 全挂退化频道页时间线且留痕、去重键=详情页 URL、
      `limit` 真实生效、`page` 上限受 max_pages 约束
- [x] `pic_url` 仅作溯源列，代码中从未下载、从未作为解析输入
- [x] 真实取数证据已入 README（3 条实测 + 正文数值样本 1368 万㎡ / 1020 万㎡ / 103.6% / 1.4 倍）
- [x] 本地自检：`python3 -m py_compile spiders/fangchan-market/spider.py` 通过
- [x] 仓级自检：`validate_manifests.py` / `conformance_gate.py --roots .` 全绿
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 首爬：触发后核对 crawl_runs 记录与行数
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身
