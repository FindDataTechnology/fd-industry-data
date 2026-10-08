# noaa-swpc 接入检查清单（批次三直建，直连免 key）

- [x] 目录名 = manifest `name`（`noaa-swpc`；入口 `run_noaa_swpc`）
- [x] `spider.py` 提供 `run_noaa_swpc(limit=N) -> list[dict]`，只依赖标准库
      `urllib` + `json`（无新依赖）
- [x] **直连复测通过**：`services.swpc.noaa.gov/products/noaa-planetary-k-index.json`
      HTTP 200（2026-10-08，无需代理出口）
- [x] 响应双形态兼容：首行表头+数组行 / 对象数组（2026-10-08 实测为后者），
      schema 漂移抛 `RuntimeError`
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] **无 `schedule` 无 `site`、无 `egress_secret`**——直连可达；静默合入，点亮排期留给人工门
- [x] golden 样本 `golden/001-noaa-swpc.json`：滚动窗统计类**只锚结构常量**
      （`source` + `period` 格式 + `min_rows`），绝不锚 Kp 数值/日期/计数
- [x] 请求失败/响应形态已变/零可解析观测均抛 `RuntimeError`（失败即红）
- [x] README 记信号选择理由 + 免 key 直连口径 + 侦察证据 + 双形态/滚动窗坑位
- [x] 本地自检：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py --roots .` 全绿
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮（人工补 `schedule`）后试跑：看 crawl_runs 记录与产出
