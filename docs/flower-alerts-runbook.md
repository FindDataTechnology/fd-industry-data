# 花卉线四级预警 — 运行手册

> change `add-flower-vertical` 波5/波6 · 写于 2026-11-08 · 本文只写**怎么用**，口径依据见
> `docs/flower-series-curation.md`，未决项见 `docs/flower-debt-ledger.md`（F1–F18）。

## 0. 一句话

一个只读脚本 `scripts/flower_alerts.py` + 一份配置 `config/flower-alerts.json`，
把花卉线拆成**四级**来判断「源活着吗 / 该发的发了吗 / 数越过线了吗 / 有什么事件」。
四级各自能分清「我们坏了」和「上游没出」，**不会把两件事混成一件**。

## 1. 跑起来

```bash
cd fd-industry-data
source ~/.config/finddata/dashboards.env      # 提供 FD_OPEN_DATA_DSN（只读也要它）

.venv/bin/python scripts/flower_alerts.py --publication   # ① 发布节奏级
.venv/bin/python scripts/flower_alerts.py --rules         # ② + ③ 阈值级（带状态机）
.venv/bin/python scripts/flower_alerts.py --events        # ④ 事件级（幂等台账）
.venv/bin/python scripts/flower_alerts.py --rules --escalate  # 越阈时另开工单
```

- **必须用 `.venv/bin/python`**：系统 `python3` 没有 `yaml`，会以 `ModuleNotFoundError` 假失败。
- 三个开关可以同时给：`--publication --rules --events`。
- **退出码 1 = 有规则越阈**（预期语义，不是崩溃）。0 = 全部未越阈。别把它当失败。
- `--dry-run`：只算不写状态（演练用）。**注意** `--publication` 和 `--events` 本来就只写报告/台账，
  状态文件只被 `--rules` 写。
- `--outdir DIR`：报告落点（默认 `reports/flower-alerts/`）。
- `--config PATH`：换一份配置（演练时用，见 §6）。

## 2. 四级各判什么

| 级 | 判据 | 结论 | 会不会出单 |
|---|---|---|---|
| ① 健康级（`fd_industry_data/health/`，不在本脚本） | 运行次数/失败连续数/新鲜度 + manifest 与遥测对账 | network / source-revision / structure / contract / source-dead | 会（经 `health_inspect.py`） |
| ② 发布节奏级（`--publication`） | 学出「发布滞后」，再看**每次运行本应看到哪一期** | `published` / `awaiting_publication` / `stream_broken` / `window_learning` / `unknown` | 只有 `stream_broken` 该处置 |
| ③ 阈值级（`--rules`） | 配置里的 comparator + threshold | `crossed` / `quiet` / `insufficient_data` | `--escalate` 时出 `contract` 工单 |
| ④ 事件级（`--events`） | 披露日历（SEC 10-Q/10-K/8-K） | 一条申报 = 一个事件 | 登记，不升级 |

### ② 的三个结论别记混

- `awaiting_publication` = **我们跑到了、上游还没发**（等下一轮，别出单）
- `stream_broken` = **我们这边断了**（水位之后有失败运行 → 断在抓取侧；水位之后一次没跑 → 调度侧没在跑）
- `window_learning` = 带水位的成功样本 < `min_samples`，**冷启动期不判**（不猜 SLA）

判据不是「最近一次运行是几点」，而是「运行日 X（学出的滞后 L）本应至少看到 X−L 那一期」。
这条是 6.2 的核心：同一时刻、同一水位，只差「之后有没有成功跑到」就能分出两种结论。

### ③ 的状态机（四个动作）

`alert`（首次越阈）→ 持续越阈 = `suppress`（不重复打扰）→ 回到阈内 = `recover`（发一次恢复）→
`cooldown`（期内再越阈仍 `suppress`，期满再越阈算新事件，`episode` +1）。
`insufficient_data` **不改状态**——数据不足既不能开也不能关一个告警，否则一次缺数会发出**假恢复**。

状态落在 `reports/flower-alerts/state/alert-state.json`（每规则一条：`state/since/episode/last_value/period/cooldown_until`）。
要看现在什么状态就 `cat` 它；要重来就把这条规则的对象删掉（不要删整文件——别的规则的状态也在里面）。

## 3. 配置怎么改（`config/flower-alerts.json`）

阈值**只在配置里**，代码内无阈值常量。改完直接重跑，不用改代码。

| 键 | 含义 | 改它的后果 |
|---|---|---|
| `defaults.cooldown_hours` | 恢复后的静默期 | 调大 = 更不容易重复打扰，也更慢发现二次恶化 |
| `rules[].threshold` + `comparator` | 阈值与方向（`lt`/`gt`/`lte`/`gte`） | 唯一的灵敏度旋钮 |
| `rules[].min_history_periods` | 至少几期才判 | 调大 = 更保守，冷启动更久判「不足」 |
| `rules[].min_bucket_samples` | 聚合桶最少样本（剔薄桶） | **别关**：斗南整周只发 1 天时，1 个样本的「周均值」能造出 −33% 假跌幅 |
| `rules[].max_period_age_days` | 期次时效闸 | **别关**：HYFM 营收序列停更，不设闸会拿 14 个月前的期次算当期利润率 |
| `rules[].aggregate` | `week_mean`/`month_mean`/`year_mean`（`_sum` 同理） | 决定「相邻两期」是什么：年频上 `year_mean` 即同比 |
| `publication.<source>.cadence` | 周期/冷启动门槛/滞后分位/宽限 | `min_samples` 调小会更早判，但样本越少滞后上界越不可信 |
| `publication.<source>.watermark_concept` | 用哪个 concept 的最新日期当水位 | 换了它等于换了 SLA 的度量对象 |

每条规则都带 `_threshold_note`：写清**这个阈值是怎么量出来的**（真库重放数字）。改阈值时同步改它，
否则下一个人会以为数字是拍脑袋定的。

`enabled: false` 的规则必须带 `blocked_reason` + `unblock`——没数据时再好的阈值也只能判「不足」。

## 4. 报告落在哪

```
reports/flower-alerts/
  <date>/publication-<date>.json     # ① 每源状态 + detail（为什么这么判）
  <date>/rules-<date>.json           # ③ 每规则读数 + per_entity + skipped + action
  <date>/tickets/                    # --escalate 时的工单（health ticket schema）
  state/alert-state.json             # 状态机（唯一会被写的东西）
  state/events.jsonl                 # ④ 事件台账（幂等：ticker|form|filed|accession）
```

`rules-*.json` 里每条的 `rule_trace`/`skipped` 是**为什么这么判**的原始依据，别只看 headline 读数。

## 5. 已知边界（别当 bug 修）

- **没有调度**：全仓没有定时跑这个脚本的任务（餐饮线同现状），只能手动/外部触发。挂账 F17。
- **斗南滚动窗 ~2 个月**：上游只留 10 个列表页，历史自接入日起累积，不宣称更长。
- **EU 花卉只有主面积**：`apro_cpsh1` 的花卉作物码只发布主面积，不是产量（挂账 F7）。
- **HYFM 利润率算不出来**：营收序列停在 2025-09-30（含代收税口径刻意不并入），超过 200 天即跳过并报原因。
- **贸易板零数据**：花卉三档未进生产排期（挂账 F6）；空图带「无数据·原因」占位。
- **年频源不会因停跑自动出单**：`absent_source_policy` 默认 `report`（零回归设计），AIPH 靠 ① 看。

## 6. 离线演练（不碰生产状态）

演练要做两件事：把阈值改成**必然越阈**，并把 `defaults.state` 指到演练目录（否则会污染生产状态机）。

```bash
cd fd-industry-data
source ~/.config/finddata/dashboards.env
D=reports/flower-alerts/$(date +%F)/drill && mkdir -p $D

.venv/bin/python - "$D" <<'PY'
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
cfg = json.loads(Path("config/flower-alerts.json").read_text(encoding="utf-8"))
cfg["defaults"]["state"] = str(out / "state.json")
cfg["defaults"]["outdir"] = str(out)
for rule in cfg["rules"]:                 # 阈值改成必然越阈（方向不变）
    if not rule.get("enabled", True):
        continue                          # 未启用的规则没有 comparator，别碰
    if rule["comparator"].startswith("lt"):
        rule["threshold"] = 1e9           # 读数一定 < 1e9
    else:
        rule["threshold"] = -1e9          # 读数一定 > -1e9
(out / "config-firing.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
print("wrote", out / "config-firing.json")
PY

.venv/bin/python scripts/flower_alerts.py --config $D/config-firing.json --rules --dry-run
```

`--dry-run` 不写状态，所以可以反复跑；去掉它才会写 `$D/state.json`（演练自己的状态，不是生产的）。
历史演练实况见 `reports/flower-alerts/2026-11-08/alert-drill/`
（5 轮：`alert×2` → `suppress×2` → `suppress` → `recover×2` → `idle×2`，末态两条规则均 `cooldown` / `episode=1`）。

**演练配置只能放 drill 目录**，绝不能覆盖 `config/flower-alerts.json`——那份是生产阈值。

## 7. 排障顺序

1. 先看 ① 有没有 `stream_broken`：这是唯一「必须现在动手」的状态。
2. 再跑 ③，看 `verdict` 是 `insufficient_data` 还是 `quiet`——两者完全不同（前者是「判不了」，后者是「没越阈」）。
3. `crossed` 但读数看起来离谱（例如 −89%）时，先看 `per_entity` 的 `denominator`（规模）与 `period`（期次），
   再判断是单价单位问题还是公司本身就巨亏——2026-11-08 的 EDBL −89.49% 就是后者（营收仅 355 万美元）。
4. 报「源未发布」先确认不是我们没跑：看 ② 的 detail 里那句「运行 … 成功、按学出的滞后本应看到 …」。
