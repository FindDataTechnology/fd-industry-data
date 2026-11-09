# 上线验收清单（2026-11-09 推代码后的一次性核对）

代码已推（github/gitee = 8046b68）。下一轮 GHA 巡检跑新代码，逐项对照：

| # | 看什么 | 旧（11-09 08:56） | 预期（新） | 不符意味着 |
|---|---|---|---|---|
| 1 | `skipped_unknown_period` 里还有没有 `at TCPConnectWrap…` | 有 | **无** | 解析器修复未生效 |
| 2 | `unregistered_sources` 字段 | 不存在 | 存在，≈`["example-stock","yfinance"]` | unregistered 分流未生效 |
| 3 | `deferred[].category` / `rule` | 无 | 有 | 顺延判词未生效 |
| 4 | `written` 里有没有 `boxofficemojo`/`cisa`/`gb-standards` | 0 张 | 可能有（若它们仍未重跑） | 若无且 latest 回到 ≥floor 则正常 |
| 5 | `healthy` | 92 | ≈101（不含 gitee-only 的话仍 92 附近） | 差很多先看 scope 是否变了 |

## 2026-11-10 更新：撤了 7 个声明后，预期要跟着改

三项验收判据**不变**（它们是"新代码在跑"的指纹，与阈值无关）：

1. `skipped_unknown_period` 不含 `TCPConnectWrap`
2. 存在 `unregistered_sources` 字段，≈`["example-stock","yfinance"]`
3. `deferred[]` 每项带 `category` 与 `rule`

但"出单源"的预期变了——7 个 B 桶声明已撤（见
`docs/min-rows-observation-baseline.md` 的 2026-11-10 追加节），所以：

| 源 | 11-10 之前 | 11-10 之后 |
|---|---|---|
| `cninfo-operating`/`portwatch-imf`/`nbs-stats`/`cpcaauto-monthly`/`eastmoney-futures`/`eastmoney-data`/`fangchan-market` | 可能有 rule1c 单 | **不应再有**；若仍出现，说明撤除未生效（或没拉到最新 main） |
| `boxofficemojo`/`cisa`/`gb-standards` | 可能有（11-06 波次） | 仍可能有；等它们下次正常调度后自愈 |

`healthy` 预期也会略降（少了 7 个声明带去的影响，但它们是健康源为主，变化应在个位数内）。

补充：完整 30 天窗本地预演 = healthy 101 / written 5 / deferred 7（含 3 张 rule1c）。
生产窗口截止时刻不同，数字会有小幅差异，**看的是三类字段的形态，不是数字相等**。

回归工具：`python3 scripts/min_rows_false_positive_report.py --days 7 --from-pg`
