# Drill 归档 — spider-heal-generate 生成流演练（2026-10-04）

萬星侧 `add-spider-heal-generate`（pack v6 第 4 技能）的验收演练夹具。**非真实数据源**：
源站为平台自身健康检查端点（`https://platform.finddatatech.cloud/healthz`，纯 JSON、无反爬），
演练完成后按计划关闭 PR、删除分支，夹具归档于此。

## 夹具

- `spiders/drill-gen-healthz/` — greenfield 生成产物快照（从分支
  `gen/drill-gen-healthz` head `6770b49` 取件）：`spider.py`（入口
  `run_drill_gen_healthz(limit)`，仅标准库 urllib）、`manifest.yaml`（v2，无 schedule/site）、
  `golden/001-drill-gen-healthz.json`（只锚 `ok==true` + 常量 `url`/`source`，不锚
  `uptimeMs`/`cells` 波动值，`scraped_at` 入 whitelist）、`README.md`、`CHECKLIST.md`。
- 工单留存原处：`reports/health-tickets/20261004-drill-gen-healthz-e1e98e04.yaml`（`kind=generate`）。

## 演练结果（全链通过）

| 判据 | 结果 |
|---|---|
| 门面 SUBMIT | ✅ 外部调用（`Idempotency-Key: drill-gen-healthz-try1`）单回合内完成全流程并回执结论 |
| 分诊互斥 | ✅ `kind=generate` → 生成流（非修复流），回执明示分诊路径 |
| PR 开出 | ✅ PR #3 `gen/drill-gen-healthz`（**已按计划关闭/不合并**；head `6770b49`，分支已删） |
| diff 边界 | ✅ 仅 `spiders/drill-gen-healthz/` 5 个新增文件；未写 `schedule`、未写 `site`、未 merge |
| golden 纪律 | ✅ 断言跨期稳定（`ok==true` + 常量字段）；明确拒绝锚 `uptimeMs`/`cells`/日期 |
| 验证链 | ✅ `health_verify --ticket …` → `verdict: ok`（golden 重放 diffs=[]、真取数 ≥1 行、manifest 0 违规、conformance gate PASS、密钥扫描 0 命中） |
| 附加 lint | ✅ `check_manifest_commands` 零漂移（`run_drill_gen_healthz` 真实存在） |
| 凭据脱敏 | ✅ PR 描述凭据仅尾四位（`…n0uG`） |

## 生成流验收要点（本演练覆盖）

1. 模板起接 → 入口命名 `run_<slug_snake>` 与 manifest `functions[].command` 对齐；
2. golden 样本强制（生成单缺失即红）与跨期稳定断言；
3. 验证链循环判绿（`verdict: ok` + exit 0）；
4. `gen/<slug>` 分支开 PR、描述附逐环证据；硬边界（不 schedule/不点亮/不 merge）零违例。

## 后续

- 首个正式生成单：`nmc-weather`（nmc.cn，真实源）——另单验收，人审 merge 由人工执行。
- 本夹具如需复跑：从本归档或 git 历史（分支已删，见 PR #3）恢复即可。