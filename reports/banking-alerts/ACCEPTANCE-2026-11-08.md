# 银行 C 线预警：真实验收记录（2026-11-08）

配套归档 `openspec/changes/archive/2026-11-08-add-banking-source-alerting`。
本文记录**哪些规则拿到了真实触发样本、哪些还没有**——不以构造样本充数。

## 1. 结论速览

| 规则 | 真实样本 | 证据 |
|---|---|---|
| `bank-penalty-new`（新增行政处罚） | **有（已上产）** | 集群 CronJob 实跑 → `crawl_runs` success / 100 行 / **39 份文书**；首次运行留痕 39 条，复跑 **0 新增** |
| `bank-disclosure-overdue`（披露逾期） | **无**（0 条逾期） | 集群 CronJob 实跑 → 100 行**全部已披露**；逾期 0 |
| 阈值 6 条（资本/一级/核心一级/杠杆/LCR/NSFR） | **无越线** | 判定升至 **104 条 / 覆盖 42 家**（资本与一级 42 家、其余 4 项 5 家），全部合规 |

机制本身由 **48 条离线单测**证明（`tests/test_alerts_core.py` 16 + `tests/test_banking_alerts.py` 32），
断言的是引擎行为；上表说的是**业务侧有没有真的触发过**，两者不混。

## 2. 上产状态（2026-11-08 13:0x 收口）

两源**已进平台排期并跑通**（此前从未被调度，根因见 §2.6）：

| 源 | CronJob | 首次运行 |
|---|---|---|
| `fd-industry-eastmoney-disclosure-plan` | `15 10 * * *` | success / 100 行 |
| `fd-industry-nfra-penalty` | `15 13 * * *` | success / 100 行 |

`crawl_sources` 两行已自动登记（`site=tencent`、`enabled=t`、排期一致）。
**首次运行已把历史事件一次性入账**（处罚 39 份文书），之后只报新增——
这样等 SMTP 配好时不会补发历史轰炸。

### 2.55 通知去重（为什么不会日刷）

- 阈值：同一 `(规则, 主体, 报告期)` 只发一次 firing；恢复发一次 resolved。
- 事件：按幂等键（披露 `代码:报告类型:年度`、处罚 `doc_id`）去重，每份文书只报一次。
- 首次 `--run` 已把历史处罚入账（39 份），故后续日巡检只报**新增**。
- 演练语义：`--dry-run` **读状态、只不写回**，与真跑结论一致
  （早先从空状态起算，演练每次报「新增 39 条」——演练不反映现实比没有演练更糟）。

### 2.6 为什么此前从未被调度（三个叠加原因）

1. **两个 spider 目录从未入库**：`spiders/nfra-penalty`、`spiders/eastmoney-disclosure-plan`
   在本地是未跟踪状态，gitee main 上没有，ApplicationSet 的 git 生成器看不到 ⇒
   没有 Application ⇒ 没有 CronJob ⇒ `crawl_runs` 零记录。（提交 `3ed7321`）
2. **`fd_industry_data/universes.py` 也未入库**：披露源在模块作用域 `from fd_industry_data
   import universes`，运行镜像 `sha-750da3f` 早于该模块 ⇒ ImportError。（提交 `d279643` + `8c65624`）
3. **chart 的 sparse-checkout 只检 `spiders/`**：`/content` 里没有 `fd_industry_data/`
   与 `universes/`，兜底导入也无处可取 ⇒ 仍 ImportError。改为
   `spiders/ fd_industry_data/ universes/` 后通过。（提交 `4650204`）

教训：**「代码写了、测过、验收过」不等于「上产」**——它可能只是躺在工作树里没提交。

## 2.5 处罚源：为什么本机跑不了，cheap1 跑得通

- 本机（Mac）：`https://www.nfra.gov.cn/cbircweb/DocInfo/SelectDocByItemIdAndChild` → **403**（IP 级 WAF）
- cheap1（`103.236.89.212`）：同 URL → **200**
- 结论：不是源坏、不是判死源，是**本机出口被封**（与 `overseas-proxy-for-crawling` 同一类判断）。

cheap1 上落地步骤（可复用）：

```bash
# 1) 只传所需子集（gitee 私有仓无法匿名 clone）
tar czf /tmp/nfra-pack2.tgz --exclude="__pycache__" \
  fd_industry_data/tabular fd_industry_data/__init__.py \
  fd_industry_data/http.py fd_industry_data/sites.py spiders/nfra-penalty
scp /tmp/nfra-pack2.tgz root@103.236.89.212:/root/
# 2) cheap1 缺 lxml（tabular/htmltable 依赖）
python3 -m pip install --break-system-packages lxml
# 3) 跑（importlib 按路径加载，因目录名带连字符）
python3 /root/nfra_run.py
```

## 3. 真实处罚数据（2026-11-08 快照）

`[nfra-penalty] docs scanned per leaf {4113: 60, 4114: 60, 4115: 60} → 42 rows`，取 40 行：

- 机关分布：总局 22 / 省局 14 / 分局 4
- **一份文书常含多个主体**：40 行只对应 **15 个 `doc_id`** → 事件按文书去重、主体全保留
- 归属结果：`matched 9` / `unattributed(no-match) 6` / `unattributed(not-a-bank) 24` / `unattributed(individual) 1`

归属到银行的样例：

```
2026-11-06 兴业银行股份有限公司            → 601166  金融监管总局
2026-11-06 中国农业银行股份有限公司咸宁分行   → 601288  咸宁金融监管分局
2026-11-06 中国银行股份有限公司十堰分行       → 601988  十堰金融监管分局
2026-11-06 中国民生银行股份有限公司宁德分行   → 600016  宁德金融监管分局
2026-11-06 中信银行股份有限公司三明分行       → 601998  三明金融监管分局
2026-06-05 中国工商银行股份有限公司          → 601398  金融监管总局
```

## 4. 归属纪律（错归属比不归属更坏）

真实数据逼出三条判定，全部落测：

1. **非银行机构不归属**：`民生人寿保险股份有限公司及相关责任人` → `not-a-bank`（24 条）。
2. **自然人不归属**：`原交通银行股份有限公司员工仇征` 归一后是「交通银行」，若不拦会**记到银行头上**；
   故先按 `员工/个人/从业人员/董事/监事/高级管理人员/客户经理` 识别自然人 → `individual`。
3. **归一名顺序敏感**：先切「及相关责任人」就不再切「股份有限公司」，会停在
   「光大银行股份有限公司」永远匹配不上。现固定五步：括号附加语 → 尾部「及相关责任人」→
   最早出现的公司形式后缀 → 去「中国」（截断后须 ≥4 字，否则「中国银行」会削成「银行」匹配全行业）→ 去「原」。
4. 归一后仍对不上就 `no-match` 保留原名（6 条），**不按相似度猜**。

## 5. 阈值源的覆盖边界（分层，别把 0 告警读成 42 家全指标正常）

覆盖**按指标分层**（2026-11-08 扩面后）：

| 指标 | 覆盖 | 来源 |
|---|---|---|
| 资本充足率、一级资本充足率 | **42/42** | 东财 F10（`NEWCAPITALADER` / `FIRST_ADEQUACY_RATIO`），无滞后 |
| 核心一级、杠杆率、LCR、NSFR | 5/42 | `cn-report` 报告抽取（试点）；F10 对这批银行恒为 null |
| 拨备覆盖率、贷款拨备率 | 数据 42/42 但**规则停用** | 区间+分档条件，单侧判会误报；有数据 ≠ 能判定 |

- 当天 **104 条判定全部合规**（quiet），数值区间 11.62–19.69，量级正常
- 报告固定输出 `coverage{covered, universe}`；未覆盖主体记 `not_covered`，既不入越阈分子也不计入达标
- 口径对齐是**逐值核过**的：F10 的 4 个字段与 harden 期 golden 锚 5 家×4 项 **20/20 一致**；
  `LIQUIDATION_RATIO` 因 108.03 ≠ golden 流动性比例 63.20 而被明确排除（名字像但不是）

## 6. 复现命令

```bash
cd fd-industry-data
export FD_CENTRAL_PG_DSN="postgresql://fd:<pwd>@100.64.0.3:30432/fd_open_data"
.venv/bin/python scripts/banking_alerts.py --dry-run --today 2026-11-08
.venv/bin/python scripts/banking_alerts.py --dry-run --today 2026-11-08 \
  --penalty-json /tmp/nfra_sample.json      # 用真实处罚样本验触发路径
.venv/bin/python -m pytest tests/test_alerts_core.py tests/test_banking_alerts.py -q
```

## 6.5 邮件通道（已用真实通道验过）

- 通道 = 本舰队既有的阿里云 DirectMail（**与 Logto 发验证码同一条**）：
  `smtpdm.aliyun.com:465`、发件人 `support@finddatatech.cloud`
- 凭据来源：`fd-prod/wire-mail-secrets`（`smtp_host` / `smtp_user` / `smtp_password`），
  运行时注入，**不落盘**
- **踩到的坑**：465 是**隐式 TLS**（`smtplib.SMTP_SSL`），不是 STARTTLS。
  通知模块原先只实现 `starttls()`，对这条真实通道根本连不上。已补 `use_ssl`
  （环境变量 `FD_ALERT_SMTP_SSL`），工作流置 `SSL=1 / TLS=0`。
- 实弹：`dispatch(...)` 返回 `sent=1 failed=0`，收件人=发件人（不打扰第三方）
- **收件人（2026-11-08 定）**：`1253774197@qq.com`、`aloadtree@gmail.com`，
  写在 `config/banking-alerts.json` 的 `notify.recipients`——收件人属配置（可版本化），
  凭据仍只走 env/Secrets。已实发验证 `sent=1 failed=0`。
- **踩到的第二个坑**：`smtp_config_from_env()` 缺收件人时直接返回 `None`，所以「事后补
  cfg.recipients」是徒劳——配置里的收件人形同虚设，且通知会被记成 `skipped`（看起来像
  「没配通道」而不是「配了没用上」）。已改为**在构造 SmtpConfig 之前把配置收件人注入 env**。
- 未配置凭据时仍一律 `skipped`，绝不假装发过

## 6.6 定时巡检（GitHub Actions，已实跑）

- 仓库 `FindDataTechnology/fd-industry-data` 是 **PUBLIC** ⇒ 免费无限 CI；自托管 runner
  `cheap-health-runner`（标签 `self-hosted, fd-health`）在线。
- `banking-alerts.yml` 已注册（active），**工作日 22:05 CST** 触发；已用 `workflow_dispatch` 实跑：
  ```
  健康级: [('eastmoney-disclosure-plan', 'ok'), ('nfra-penalty', 'ok')]
  阈值级: 30 条判定 | 覆盖 5 家 / 宇宙 42
  事件级: 披露 100 行全部已披露 | 处罚 39 文书 / 新增 0
  banking_alerts exit=0
  ```
- **注意**：仓库主推 gitee，GitHub 是镜像。**只推 gitee 的话工作流文件在 GitHub 上不存在、
  Actions 永远不会调度**——本次就因此实际空转了一次。两端都要推。

### 6.6.1 两个让流水线假绿的缺陷（都已修）

1. **runner 没有 psycopg2**：自托管 runner 的系统 python 缺驱动（runner **镜像**里有，但工作流
   不在镜像里跑）。花卉线 F17 栽过同样的坑，解法一致：`_psql()` 子进程回退，三条查询都走它。
   实测强制回退路径：比率 5 家、两源 crawl_runs、200 条处罚 json_agg 全部通过。
2. **exit 1 有两种含义**：Python 未捕获异常的退出码也是 1，而工作流把 1 当「有通知发送失败」
   的**预期语义**放行 ⇒ 脚本崩了也报绿（首次运行就是这样：job success，实际 `ModuleNotFoundError`）。
   已加 `_guarded_main()`：未捕获异常收敛为 **exit 2**，1 只保留原意。

顺带修了一个真 SQL 错：`json_agg()` 不能按非聚合列排序，items 查询改为在子查询里按 run 时间取子集后再聚合。

## 7. 未决

- ~~处罚源尚未纳入平台排期~~ **已解决**：两个源均已进排期并成功运行（见 §2）。
  本机 403 的问题不影响上产——集群 tencent 出口对两个端点均 200。
- `party_aliases` 现覆盖 15 家大行/股份行全称；城商行/农商行（如 600919 江苏、002142 宁波）
  只在部分文书出现，遇新主体应**补别名表**而不是放宽匹配规则。
- 阈值层 5/42 的覆盖要扩，属 B 线「比率抽取扩面」议题，不在本线。
