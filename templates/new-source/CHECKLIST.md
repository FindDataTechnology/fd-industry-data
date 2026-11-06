# 新源接入检查清单（unified-source-onboarding）

复制本目录到 `spiders/<my-source>/`，逐项勾掉。全程只有 git 提交与平台自助操作——
不新建仓库、不发镜像、不碰常驻组件（这是 openspec crawl-platform 的契约）。

- [ ] 目录名 = manifest `name`（连字符目录的入口函数把 `-` 换 `_`：`run_my_source`）
- [ ] `spider.py` 提供 `run_<name>(limit=N) -> list[dict]`，只依赖镜像预装依赖
      （scrapling/httpx/lxml/psycopg2 等；新依赖 = 发版决策，先别加）
- [ ] manifest 字段齐全：name/label/source_url/functions/columns/entities/fetch
- [ ] `site` 只在需要离开 tencent 时才写（合法值见 `fd_industry_data/sites.yaml`）
- [ ] **不写 schedule**——先静默合入，跑通再点亮
- [ ] 本地自检全绿：
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
      首爬成功后的运行按 schedule 增量推进；存量已产源（有成功记录）不走
      首爬路径。参考实现：`spiders/abs-sdmx/spider.py`（窗口过滤先于 limit
      截断——切片自最老月份升序时必须先过滤再截断）
- [ ] 需要指标语义时，另行走统一指标注册目录治理（列→绑定→verified→标识符），
      不阻塞爬取本身
