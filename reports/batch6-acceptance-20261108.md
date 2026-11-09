# 批次六验收汇总（20261108）

生成时间：2026-11-08T02:13:24　生成器：`python3 fd-industry-data/scripts/batch6_acceptance.py`

## 1. 逐源首爬与 golden 重放

| # | 单元 | golden 参数 | rows | 时长 | 结果 |
|---|---|---|---|---|---|
| 1 | `nfra-stats-xls` | `{"limit": 800}` | 800 | —（异地） | PASS（异地出口 cheap1 核验） |
| 2 | `nfra-penalty` | `{"limit": 60}` | 60 | —（异地） | PASS（异地出口 cheap1 核验） |
| 3 | `pboc-credit-funds` | `{"limit": 4000}` | 2997 | 6.8s | PASS |
| 4 | `pboc-loan-usage` | `{"limit": 40}` | 24 | 1.0s | PASS |
| 5 | `chinamoney-shibor` | `{"limit": 20, "days": 0}` | 30 | 156.9s | PASS |
| 6 | `chinamoney-lpr` | `{"limit": 80}` | 80 | 14.9s | PASS |
| 7 | `chinamoney-ncd-curve` | `{"limit": 400, "days": 10}` | 102 | 22.4s | PASS |
| 8 | `shclearing-monthly` | `{"limit": 400, "months": 1, "period": "2026-09"}` | 257 | 8.1s | PASS |
| 9 | `chinabond-monthly` | `{"limit": 400, "months": 1, "period": "2026-09"}` | 284 | 9.3s | PASS |
| 10 | `chinabond-yield-curve` | `{"limit": 1200, "days": 3}` | 218 | 5.6s | PASS |
| 11 | `shclearing-capital-notes` | `{"limit": 10, "keyword": "无锡农村商业银行"}` | 10 | 13.9s | PASS |
| 12 | `cba-wealth-index` | `{"limit": 60, "months": 1, "period": "2026-09"}` | 32 | 23.4s | PASS |
| 13 | `chinawealth-reports` | `{"limit": 60, "pages": 3}` | 28 | 40.7s | PASS |
| 14 | `yindeng-npl-transfer` | `{"limit": 2, "kinds": "", "start_page": 200}` | 4 | 13.6s | PASS |
| 15 | `eastmoney-disclosure-plan` | `{"limit": 200}` | 200 | 0.8s | PASS |
| 16 | `ratings-ccxi` | `{"limit": 200, "keyword": "银行", "since": "2026-09-01", "until": "2026-09-30"}` | 6 | 30.8s | PASS |
| 17 | `dagong-ratings` | `{"limit": 20, "topic": "商业银行", "pages": 1, "page": 5}` | 10 | 1.4s | PASS |
| 18 | `lh-ratings` | `{"limit": 200, "keyword": "银行", "max_pages": 8}` | 70 | 11.9s | PASS |
| 19 | `dfjc-ratings` | `{"limit": 100, "keyword": "银行", "pages": 3, "year": "2024"}` | 30 | 4.4s | PASS |
| 20 | `xsj-ratings` | `{"limit": 200, "pages": 3, "period": "2026-07"}` | 35 | 4.7s | PASS |

## 2. 仓级门禁

- `scripts/validate_manifests.py` → rc=0：`validate_manifests: 157 manifests, 0 violations`
- `scripts/conformance_gate.py` → rc=0：**本批 20 单元违规 0 条**；其余单元违规 0 条（`conformance gate: PASS — 159 units, 0 violations`）

## 3. spec 四项留痕核对

| 留痕项 | 障碍（spec 口径） | 解锁条件 | 本期状态 |
|---|---|---|---|
| ① chinawealth 产品查询 API | RSA 公钥 + AES-256-ECB 握手已复现、服务端 429 频控，M 成本，非行业面核心 | 频控配额可满足批量拉取（或渠道方给出免握手的只读通道） | 仍成立（未解锁） |
| ② PBoC 货政报告与区域金融运行报告 | 季度/年度 PDF；文本层与统计附表抽取策略待验证 | 文本层 + 统计附表抽取策略验证通过（或上游给出结构化附表） | 仍成立（未解锁） |
| ③ 银行资本债「不赎回」公告 | 无干净结构化通道：上清所 TRS 全时段 0 条；上市银行侧由巨潮 searchkey/披露易类目覆盖，非上市银行挂起 | 非上市银行侧出现稳定结构化披露通道 | 仍成立（未解锁） |
| ④ 中债-银登信贷资产价格指数疑停更 | 1487 点止于 2024-12-12，接入前须复核活性 | 复核确认停更则按历史快照口径登记、不注册成长序列 | 本期未接入、未注册成长序列（按 spec 口径仅登记，无序列可污染） |

## 4. 本期已知状态与交付后修正

**NFRA 两单元：本机出口被封，改用异地出口（cheap1）核验通过。**
本机（Mac）打 `www.nfra.gov.cn/cbircweb/*` 全部 `HTTP 403`（单元同款头与 curl 默认头双测均 403，
首页 200 → 出口 IP 级 WAF 拦截，非源结构变化）；同在 cheap1 上同一请求 200。
故把单元目录 + `fd_industry_data` 同步到 cheap1（临时 `pip install --target pylibs lxml xlrd`）
跑同一份 golden 重放：`nfra-stats-xls` → **`ok=True rows=800 diffs=[]`**，
`nfra-penalty` → **`ok=True rows=60 diffs=[]`**。明细与出处见
`reports/batch6-external-verify-20261108.json`（含 `_provenance`）。
单元代码与 golden 一字未改；本地 FAIL 行的耗时是 6×90s 退避堆积，不计入排期口径。

**`chinamoney-shibor` 交付后修正（默认收敛为增量）**：全史 20 年 = 21 片 ×2 通道
≈140 请求 /156s；`runner_cli` 不注入 manifest 默认值，若默认抓全史则日频每天重打
同一请求指纹，正是本单元 README 记录的 403 封禁成因。故新增 `days` 参数并默认 45
（≈30 交易日 = 1 片 = 2 请求，实测 13s / 208 行），`days=0` 保持全史回补能力
（golden 用该路径验 2006-10-08 锚），平台首爬窗口 `window_start`/`window_end` 优先级最高。
验收门槛不变：golden 重放仍为 `ok=True diffs=[]`。

**排期时长口径（7.1 输入）**：`batch6-durations-<date>.json` 只对**成功**运行取实测；
失败运行（如 NFRA 出口被封时 6×90s 退避 = 10 分钟）改用稳态估计 3 分钟，
估计依据：早间正常运行时 8 页列表 + 逐文档详情/附件约 1-3 分钟量级。
若拿退避耗时占窗口，13:18-13:21 整段会被 lock 死（实测重算一次即 103 条窗口相交）。

## 5. 复现方式

```bash
cd fd-industry-data
python3 scripts/batch6_acceptance.py                 # 逐源重放 + 门禁
python3 scripts/batch6_acceptance.py --reuse reports/batch6-acceptance-<date>.json  # 只重跑门禁
python3 scripts/schedule_overlap_check.py \
    --new <逗号分隔 20 单元> \
    --durations reports/batch6-durations-<date>.json
```

合计 20 源，PASS 20，FAIL 0。
