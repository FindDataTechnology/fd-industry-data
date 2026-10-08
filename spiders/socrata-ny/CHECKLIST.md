# socrata-ny 接入检查清单（批次二 Wave C 直建，fx01 出口）

- [x] 目录名 = manifest `name`（`socrata-ny`；入口 `run_socrata_ny`）
- [x] `spider.py` 提供 `run_socrata_ny(limit=N) -> list[dict]`，只依赖标准库
      `urllib` + `json` + `urllib.parse`（无新依赖）
- [x] **经 fx01 代理出口复测通过**：`data.ny.gov/resource/k7gz-mn77.json` HTTP 200
      （冒烟 5 行，价格字段齐全）
- [x] 单元代码不硬编码代理地址（urllib 默认尊重 `HTTPS_PROXY`/`HTTP_PROXY` 环境变量）
- [x] manifest 声明 `egress_secret: fd-industry-egress-fx01`（运行时注入出口）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] **无 `schedule` 无 `site`**——静默合入，点亮留给人工门
- [x] golden 样本 `golden/001-socrata-ny.json`：价格类**只锚结构常量**
      （`source` + `period` 格式 + `min_rows`），不锚当期价格
- [x] 请求失败/响应非数组/零可解析行均抛 `RuntimeError`（失败即红）
- [x] README 记数据集选择理由 + 代理要求 + 侦察证据 + URL 编码坑
- [x] 本地自检：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py --roots .` 全绿
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮（人工补 `schedule`）后试跑：看 crawl_runs 记录与产出
