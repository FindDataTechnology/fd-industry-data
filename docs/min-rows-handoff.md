# 交接：行数下限这条线（2026-11-10）

给接手的人。只写「你现在需要知道的」，不重复历史。

## 一句话现状

45 个声明 → **最终 38 个**（撤了 7 个结构性误报的），规则 1c 判「最近一次可比成功」，
代码与文档全部在 gitee + GitHub 的 `main` 上。**阈值不再调整**——数据说现值是对的。

## 线上有什么（都可 git 读到）

| 文件 | 作用 |
|---|---|
| `docs/min-rows-coverage-baseline.md` | 分桶口径 + §3/§3.1/§3.2 的 45 源取值表 |
| `docs/min-rows-calibration-analysis.md` | **最重要**：ε 反解、样本量前提、8.2 为何被否 |
| `docs/min-rows-observation-baseline.md` | 观察起点快照（45 源状态）+ 7 源撤销标注 |
| `docs/min-rows-acceptance-checklist.md` | 上线验收三项指纹 |
| `docs/min-rows-declaration-status.md` | 全过程 + 错题表（我犯的 5 类错） |
| `docs/min-rows-buckets-2026-11-09.{csv,md}` | 133 源五桶全量 |
| `docs/min-rows-d-bucket-pending.md` | 67 个样本不足源，等 n≥5 再判 |
| `scripts/declare_min_rows.py` | 可重放的声明脚本（38 声明 + 11 SKIPPED 带理由） |
| `scripts/min_rows_false_positive_report.py` | 误报率报表（只读） |

## 四个未完成项，各自的归属

### 1. 观察一周（任务 3.2）—— 只有时间
- 工具：`python3 scripts/min_rows_false_positive_report.py --days 7 --from-pg`
- 基线：`docs/min-rows-observation-baseline.md`
- **已了结（2026-11-10，`14d9634`）**：那三张单是 11-06 09:40–10:20 的批量探针误报（48 源各一次、
  恰好 20 行、`image_tag=unknown`），已 `closed` 并**暂撤三源声明**。理由：关单是终态，
  不撤声明下一轮会重新开单（已实测复现）。恢复日 gb-standards 11-12 / cisa 11-15 / boxofficemojo 11-17
  （成因是 11-06 平台小 limit 波次：48 源各跑一次、恰好 20 行、`image_tag=unknown`）
- 回退判据：某源出现「出单 → 立即恢复 → 再出单」的反复，才说明阈值过敏；**单纯"出单后又达标"不算误报**

### 2. 抽查真跑（任务 2.3）—— 已裁决不手动跑
等价证据：新遥测 SQL 对真库实跑返回 8 列 / 真实 30 天回放 45 源 42 个零误报 /
`partial` 端到端用例 / 两侧读取器 45/45 同值。**下次计划内调度即这次抽查。**

### 3. `blocked-config` 的巡检侧（任务 7.7）—— 交给正在写该块的会话
`requires_env` 预检会写 `status="blocked-config"`。我已让解析器接受它（否则被当非遥测丢掉），
**但缺一半**：「只有 blocked-config 的源」仍被判 `rule2 需人工`，与 runner 侧「不催修复工单」矛盾。
该做的：最近一次是 blocked-config 的源归入 `Plan.blocked_config`，可见但不判定。

### 4. 生成态不进 git（任务 7.5）—— 治理裁决
每次 sync 合并都在 `reports/*/events*.json`、`rules-*.json`、`*.jsonl` 上重新冲突
（上次预演 24 个冲突里 13 个是这类）。要么生成态出 git，要么单写目标 + 镜像。

## 五个会咬人的坑（都是实测过的）

1. **有合法零行质量的源不能声明行数下限。** 任何正下限都会按零行占比出单（2%–17%）。
   这类源需要「N 轮中 M 轮为空」的跨轮判据，不是阈值高低问题。
2. **ε 分位只在样本够大时才成立。** n 个样本能估的最小分位约 `1/n`；
   38 个声明源里 `n≤9` 有 16 个，那些注释不能署 ε，只能写"取观测最小值；语义=任何新低出单"。
3. **`error_head` 必须单行。** 带换行的错误（Node 调用栈）会把一条遥测撑成多条 TSV 物理行，
   旧解析器把第二行读成源名。已在 writer 侧 `sanitize_error_head()` 折叠空白。
4. **`running` / `blocked-config` 是合法遥测，但不是判据。** 状态校验写成"只认四种"会丢真记录；
   但也不能让它们顶替 `last_success`，否则卡住的 run 会让源看起来健康。
5. **`max_daily_tickets` 默认 5。** 复现/验收时**必须先提上限**，否则拿到的是被截断的假数字
   （我第一次就拿"5 张"当过结论，实际 10 张）。

## 三条命令够用了

```bash
# 复算分桶与阈值依据（只读）
dashboards/sql/fd_open_data.sh < <(sed -n '/## 5. 复算 SQL/,/^```$/p' docs/min-rows-coverage-baseline.md)

# 误报率（观察期每周一次）
python3 scripts/min_rows_false_positive_report.py --days 7 --from-pg

# 校验改动没破协议
.venv/bin/python scripts/validate_manifests.py     # 期望 162 manifests, 0 violations
```

## 一个提醒

这个仓是多会话并发的。我这一场踩过两次：
- `git add -A` 把别人的在途工作扫进我的提交（已记纪律：提交前必看 `git diff --cached --stat`）
- 别人用 `commit -a` 把我的未提交改动扫进他们的提交

**动手前先 `git status`，提交前先对账。** 另外，共享工作树里跑测试可能读到别人写了一半的中间态
——若红的几条都指向同一个刚改过的文件，先看 mtime。
