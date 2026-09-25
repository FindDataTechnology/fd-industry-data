# 新数据源接入（平台路径）

> 本文替代旧的「宏观十步接入配方」。镜像前置步骤已随 gitops-crawl-runtime 消失；
> registry 治理（列→绑定→verified→标识符→series 波次）仍是概念线的独立后续，
> 不再阻塞爬取上线。契约见 openspec `unified-source-onboarding`。

## 三步主路径

```
建目录 → gate 绿 → 点亮
```

1. **建目录**：`cp -r templates/new-source spiders/<my-source>/`，改 manifest 与
   `run_<name>`，照 `templates/new-source/CHECKLIST.md` 逐项勾。schedule 留空 =
   静默合入。
2. **gate 绿**：push 触发 Jenkins `fd-industry-runner`（gate 阶段在镜像同构环境跑
   validate_manifests + conformance gate + 全源 import 扫描）；本地可先跑：
   `python3 scripts/validate_manifests.py && python3 scripts/conformance_gate.py --roots .`
3. **点亮**：Console/`platform_trigger` 先试跑一次看产出，再补 `schedule` 提交。
   ApplicationSet ~3 分钟渲染出该源 CronJob；dispatcher 每 5 分钟把 manifest 镜像进
   `crawl_sources`（Console 源清单的数据来源）。

## 老配方 → 新路径对照

| 旧十步（宏观配方） | 现在 |
|---|---|
| 0. 镜像前置（先构建分发新镜像再动 DB） | **已删除**——内容走 git，镜像低频发版 |
| 1–5. 列/绑定/verified/标识符 | 概念线后续治理（unified-indicator-registry），可在点亮后独立做 |
| 6. 适配器 | 行业源不需要；概念线仍在 fd-open-data-mcp |
| 7–8. 波次/重启 | 行业源用 manifest schedule；概念线 policy 不变 |
| 9–10. 验证与坑 | trigger-now 试跑 + crawl_runs 遥测，所见即所得 |

## 特殊源与联邦成员

- 无法归一为 `spiders/<src>/` 的特殊 Job（wenshu 等）：运行建制不动，以最小 helper
  向 `crawl_runs` 报到（`scripts/report_special_run.sh`）+ `crawl_sources` 登记，
  Console 即可见。
- scrapyd 线（law/concept）：联邦成员，报到即可，不迁移。
- 触发例外（立即跑/回填/临时改频）：Console 或 `platform_trigger` 写
  `pending_runs`，目标站点 dispatcher 认领执行——永不直达执行节点。
