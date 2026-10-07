# amis-market 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`amis-market`；入口 `run_amis_market`）
- [x] `spider.py` 提供 `run_amis_market(limit=N) -> list[dict]`，只依赖标准库 `urllib`/`json`（无新依赖）
- [x] **需代理出口**：端点 `amis-9189b.appspot.com` 被墙；单元用 urllib 缺省代理行为（尊重 `HTTPS_PROXY`/`HTTP_PROXY` 环境变量），**不硬编码代理地址**；README 首屏标注
- [x] **manifest 无 `schedule` 无 `site`**——代理注入就绪前不点亮，静默合入，点亮留给人工门
- [x] 端点免 key 无鉴权；无重试放大；50000 行帽命中按失败处理（拒绝不可靠截片）
- [x] `max_lastupdate=1` 只取最新修订（同键多修订行实测去重，自然键唯一）
- [x] 三段名整体 backtick（`fao-maps.fao_amis.*`，项目段含连字符）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch；`columns` 与 spider 行字段逐一对齐
- [x] golden 样本 `golden/001-amis-market.json`（三参窄化单序列；锚深历史 2017 营销年冻结值 760.318808 + 常量；`last_update` 进 whitelist）
- [x] 本地自检（经代理）：`spider.py` 冒烟通过；入口函数与行数在 PR body 手工留证（无工单直建）
- [x] 仓级自检：`validate_manifests.py` / `check_manifest_commands.py` / `conformance_gate.py --roots .` 全绿
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由人工/CI 确认
- [ ] 集群 runner 代理注入上线（tasks 4.2）后：试跑 Console「立即触发」，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule`（±`site` 执行位）提交——**人工门，spider-heal 不点亮**
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身
