# steam-ccu 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`steam-ccu`；入口 `run_steam_ccu`）
- [x] `spider.py` 提供 `run_steam_ccu(limit=N) -> list[dict]`，只依赖标准库 `urllib`/`json`/`time`（无新依赖）
- [x] **需代理出口**：api/store.steampowered.com 双域被墙；单元用 urllib 缺省代理行为（尊重 `HTTPS_PROXY`/`HTTP_PROXY` 环境变量），**不硬编码代理地址**；README 首屏标注
- [x] **manifest 无 `schedule` 无 `site`**——代理注入就绪前不点亮，静默合入，点亮留给人工门
- [x] 端点免 key 无鉴权；单 app 两请求 + 0.2s 间隔，无重试放大
- [x] CCU 实时数：golden **只锚结构断言 + min_rows，绝不锚任何数值**（README 口径说明）
- [x] 坏 appid 双端不一致（CCU 404 / 评论 200+空）已处理：两路任一失败整 app 跳过留痕；全部失败抛 `RuntimeError`
- [x] 种子名称为代码常量元数据（端点不回名称），README 注记
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch；`columns` 与 spider 行字段逐一对齐
- [x] golden 样本 `golden/001-steam-ccu.json`（`min_rows: 5` + `appid`/`name`/`source` 常量锚；实时/漂移字段全部进 whitelist）
- [x] 本地自检（经代理）：`spider.py` 冒烟通过；入口函数与行数在 PR body 手工留证（无工单直建）
- [x] 仓级自检：`validate_manifests.py` / `check_manifest_commands.py` / `conformance_gate.py --roots .` 全绿
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由人工/CI 确认
- [ ] 集群 runner 代理注入上线（tasks 4.2）后：试跑 Console「立即触发」，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule`（±`site` 执行位）提交——**人工门，spider-heal 不点亮**
- [ ] 指标语义治理（`entity_type: product` 定名、列→绑定→verified→标识符）另行推进，不阻塞爬取本身
