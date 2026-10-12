# cninfo-operating 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`cninfo-operating`；入口 `run_cninfo_operating`）
- [x] `spider.py` 提供 `run_cninfo_operating(limit=N) -> list[dict]`
      ——只依赖标准库 `json/urllib/re/datetime/time`，**无 PDF 库、无三方库**（spec 硬约束：公告源走文本通道）
- [x] manifest 字段齐全：version/name/label/source_url/scanner_mode/ranking_seed/functions/columns/concepts/entities/fetch
- [x] manifest `columns` 与 spider 实际产出字段逐一对应（12 列，已核对）
- [x] `site` 未写（只访问 www.cninfo.com.cn 与 *.eastmoney.com 公开 JSON，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门
- [x] golden 样本 `golden/001-cninfo-operating.json`：
      锚固定历史窗口 `2026-03-01~2026-04-30`（不锚滚动当月）+ 不可变站点标识符
      （`announcement_id` / `art_code`）+ 五类 `category` 全覆盖；实测重放 `ok=True, rows=12`
- [x] 坑位已落实并写进 README：详情页无正文 / announcementId≠art_code / 港股 REITs 查不到 /
      `<em>` 剥离 / 列表翻页下限 / 搜索词轮转交织 / 巨潮 504
- [x] 容错已落实：单次尝试不重试、同源 ≥0.4s、匹配不到跳过不猜数、正文 <50 字跳过、
      去重键 `(sec_code, announcement_id)`、`limit` 真实生效（循环早停 + 出口截断）
- [x] `adjunct_url` 仅作溯源列，代码中从未下载、从未作为解析输入
- [x] 真实取数证据已入 README（含样例行 JSON 与条数；农业/房地产/金融三领域均有命中）
- [x] 本地自检：`python3 -m py_compile spiders/cninfo-operating/spider.py` 通过
- [x] 仓级自检：`validate_manifests.py` / `conformance_gate.py --roots .` / `check_manifest_commands.py` 全绿
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）——**人工门，建造代理不点亮**
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身
