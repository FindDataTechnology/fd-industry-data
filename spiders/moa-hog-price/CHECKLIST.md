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
- [ ] 需要指标语义时，另行走统一指标注册目录治理（列→绑定→verified→标识符），
      不阻塞爬取本身
