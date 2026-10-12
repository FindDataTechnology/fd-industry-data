# nbs-housing 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`nbs-housing`；入口 `run_nbs_housing`）
- [x] `spider.py` 提供 `run_nbs_housing(limit=N, dataset="all") -> list[dict]`，
      只依赖镜像预装依赖：stdlib（asyncio/logging/re/datetime/html/urllib）+ scrapling 系
      （`FetcherSession` 取页、`lxml` 解析）；无新增第三方库
- [x] manifest 字段齐全：version/name/label/source_url/functions/columns/concepts/entities/fetch
- [x] `site` 未写（只访问 www.stats.gov.cn 公开静态 HTML，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门
- [x] golden 样本 `golden/001-nbs-housing.json`（只锚跨月稳定的结构位与口径，不锚当月数值）
- [x] 板块发现优先于硬编码：入口从 `sj/zxfb/` 列表页按标题正则取最新文章，无写死文章 URL
- [x] 语义不混类：表1/表2（销售价格指数）产出；表3/表4（分类指数）按标题识别后显式跳过并留痕
- [x] 缺测处理：`—`/空 记 `None`，绝不填 0；1 月发布无累计列时 `ytd_avg` 为 None
- [x] 与 `spiders/nbs-stats` 边界清晰：该单元走 easyquery JSON，本单元走文章 HTML 表格，未改动既有单元
- [x] 本地自检：
      `python3 -m py_compile spiders/nbs-housing/spider.py` 通过；
      `validate_manifests.py` / `conformance_gate.py --roots .` 全绿；
      `load_spider_module('nbs-housing','spiders')` + `run_nbs_housing(limit=20)` 实取 20 行
- [x] golden 重放：`replay_sample` → `ok=True, rows=60, diffs=[]`（实弹网络）
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）——**人工门**
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身
