# 新源接入检查清单（unified-source-onboarding）· quotes-toscrape-com

按 `templates/new-source/CHECKLIST.md` 起接；平台侧步骤（dispatcher tick / 试跑 / 点亮）
未在本回合内做，留给人工门（本单为 UI 目检 drill，人工裁决不点亮）。

- [x] 目录名 = manifest `name`（`quotes-toscrape-com` → 入口 `run_quotes_toscrape_com`）
- [x] `spider.py` 提供 `run_quotes_toscrape_com(limit=N) -> list[dict]`，只依赖镜像预装依赖
      （本单元仅用标准库 `urllib` + `html.parser`；未加新依赖）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/entities/fetch
- [x] `site` 未写（公开练习站，走默认执行位 tencent）
- [x] **未写 `schedule`**——静默合入，点亮由人工决定
- [x] golden 样本 `golden/001-quotes-toscrape-com.json`：`min_rows >= 5`，只锚常量
      （`source` / 首页 `url` / 首页首条作者）与行数下限，不锚日期或波动数值，
      `scraped_at` 入 whitelist
- [x] 本地自检全绿：`python3 scripts/validate_manifests.py`（0 违规）
      与 `python3 scripts/conformance_gate.py --roots .`（PASS）
- [x] `scripts/health_verify.py --ticket reports/health-tickets/20261012-wire-DE-20261013-U1.yaml`
      → `verdict: ok`
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`）——合入前由平台侧确认
- [ ] 等 dispatcher tick（≤5 分钟），Console 源清单出现该源——人工/平台侧
- [ ] 试跑 / 点亮（manifest 补 `schedule` 提交）——**人工决定，本 Agent 不写 schedule、不点亮**
