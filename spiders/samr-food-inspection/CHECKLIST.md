# 新源接入检查清单（unified-source-onboarding）

复制本目录到 `spiders/<my-source>/`，逐项勾掉。全程只有 git 提交与平台自助操作——
不新建仓库、不发镜像、不碰常驻组件（这是 openspec crawl-platform 的契约）。

- [x] 目录名 = manifest `name`（连字符目录的入口函数把 `-` 换 `_`：`run_samr_food_inspection`）
- [x] `spider.py` 提供 `run_samr_food_inspection(limit=N) -> list[dict]`，只依赖镜像预装依赖
      （scrapling/lxml/stdlib；新依赖 = 发版决策，先别加）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/entities/fetch
- [ ] `site` 只在需要离开 tencent 时才写（合法值见 `fd_industry_data/sites.yaml`）
      —— 本单元直连 www.samr.gov.cn（国内源，无代理/无鉴权），走缺省 tencent，不写 site
- [x] **不写 schedule**——先静默合入，跑通再点亮（`data_period: irregular` 已声明）
- [x] 本地自检全绿：
      `python3 scripts/validate_manifests.py && python3 scripts/conformance_gate.py --roots .`
      （从仓根跑；在 finddata 工作区根则加 fd-industry-data/ 前缀）
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`，红则修完再推）
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）
- [ ] 知悉首爬回补（industry-crawl-gitops）：点亮后的首次调度运行由 dispatcher
      自动按点亮日计算「滚动 12 个整月」回补区间（整月截断：起点回溯 12 个月
      取当月 1 号，终点=点亮日），以 `window_start`/`window_end` 运行参数派发
      ——无需人工传参。可见性：区间落在 pending_runs.params（派发时）与
      crawl_runs.metrics（执行后）；首爬失败留失败记录，下个节拍自动带窗重试。
      源单元要真正按窗口取数：入口签名显式声明 `window_start`/`window_end`
      两个可选参数即可被 kwargs 注入（runner 同时经 `FD_WINDOW_START`/
      `FD_WINDOW_END` 环境变量暴露给所有 spider）；不声明则窗口仅记录不生效。
      本单元暂未声明窗口参数：公告列表明细已在站点侧完整留存（2019 年起），
      首爬回补靠 max_pages 调深即可；确需窗口取数时再显式声明。
      参考实现：`spiders/abs-sdmx/spider.py`（窗口过滤先于 limit 截断）
- [ ] 需要指标语义时，另行走统一指标注册目录治理（列→绑定→verified→标识符），
      不阻塞爬取本身（本单元已声明 declaration-only 绑定：title → food.safety_notice）

## 本单元专项

- [x] 字段级口径（任务 2.4）：result_category 三值口径 + restaurant_related 标注
      编码于 `spider.py` TITLE_RULES/BODY_RULES，规则表与人工判定对照见 README
      （6/6 一致，验收准则满足）
- [x] golden 锚定结构常量（source / result_category / restaurant_related），
      不锚定具体标题/URL/日期；重放 `fd_industry_data.health.guard.replay_sample`
      → ok=True, rows=20, diffs=[]
- [x] 直连网络口径（无代理、timeout=30s、0.5-0.9s 抖动、retries=1）写入 README 与 spider.py 文档头
