# jp-estat 接入检查清单（unified-source-onboarding）

全程只有 git 提交与平台自助操作——不新建仓库、不发镜像、不碰常驻组件
（这是 openspec crawl-platform 的契约）。

- [x] 目录名 = manifest `name`（`jp-estat`；入口 `run_jp_estat`）
- [x] `spider.py` 提供 `run_jp_estat(limit=N) -> list[dict]`，只依赖标准库
      `urllib`/`json`/`os`（无新依赖）；key 只从 env 读取（`ESTAT_APP_ID`），
      绝不硬编码/入文件，行内 `source_url` 的 appId 一律 `REDACTED`
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] `site` 未写（只访问 api.e-stat.go.jp 公开 REST，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮留给人工门（Wave B 纪律）
- [x] golden 样本 `golden/001-jp-estat.json`（锚 statsDataId 0000150041 固定历史期值：
      time 1993000000 / cat01 001 / unit 人 / value 124451938.0，min_rows 50）
- [x] 本地自检：`health_verify.py --ticket reports/health-tickets/20261007-jp-estat-55b64112.yaml` → `verdict: ok`
- [x] 仓级自检：`validate_manifests.py` / `check_manifest_commands.py` / `conformance_gate.py --roots .` 全绿
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
      （官方无数值化限速，保持低频分页；大规模持续利用按官方惯例事先联络）
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）——**人工门，spider-heal 不点亮**
- [ ] 指标语义治理（列→绑定→verified→标识符）另行推进，不阻塞爬取本身
