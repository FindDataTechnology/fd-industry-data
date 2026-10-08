# ourairports 接入检查清单（批次三直建，直连免 key）

- [x] 目录名 = manifest `name`（`ourairports`；入口 `run_ourairports`）
- [x] `spider.py` 提供 `run_ourairports(limit=N) -> list[dict]`，只依赖标准库
      `urllib` + `csv` + `io`（无新依赖）
- [x] **直连复测通过**：`davidmegginson.github.io/ourairports-data/airports.csv`
      HTTP 200（2026-10-08，12.7 MB，无需代理出口）
- [x] **流式解析**：`io.TextIOWrapper` + `csv.DictReader` 逐行读，绝不一次
      `read()` 全量 12.7 MB（防 OOM）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] **无 `schedule` 无 `site`、无 `egress_secret`**——直连可达；静默合入，点亮排期留给人工门
- [x] golden 样本 `golden/001-ourairports.json`：快照计数类**只锚结构常量**
      （`source` + `period` 格式 + `min_rows`），绝不锚当期计数/类型枚举集合
- [x] 请求失败/无 type 列/零有效行均抛 `RuntimeError`（失败即红）
- [x] README 记信号选择理由 + 免 key 直连口径 + 侦察证据 + 流式/月更快照坑位
- [x] 本地自检：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py --roots .` 全绿
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮（人工补 `schedule`）后试跑：看 crawl_runs 记录与产出
