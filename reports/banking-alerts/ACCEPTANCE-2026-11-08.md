# 银行 C 线预警：真实验收记录（2026-11-08）

配套归档 `openspec/changes/archive/2026-11-08-add-banking-source-alerting`。
本文记录**哪些规则拿到了真实触发样本、哪些还没有**——不以构造样本充数。

## 1. 结论速览

| 规则 | 真实样本 | 证据 |
|---|---|---|
| `bank-penalty-new`（新增行政处罚） | **有（已上产）** | 集群 CronJob 实跑 → `crawl_runs` success / 100 行 / **39 份文书**；首次运行留痕 39 条，复跑 **0 新增** |
| `bank-disclosure-overdue`（披露逾期） | **无**（0 条逾期） | 集群 CronJob 实跑 → 100 行**全部已披露**；逾期 0 |
| 阈值 6 条（资本/一级/核心一级/杠杆/LCR/NSFR） | **无越线** | 5 家 × 6 条 = 30 条判定全部合规（quiet） |

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

## 5. 阈值源的覆盖边界（别把 0 告警读成 42 家正常）

- 有比率观测的只有 **5 家**（`source_used='cn-report'`，17 个概念，最新期 2025-12-31）
- 报告固定输出 `coverage{covered:5, universe:42}`；其余主体记 `not_covered`，既不入越阈分子也不计入达标
- 当天 30 条判定全部合规 → `distinct verdicts: {'quiet'}`
- 停用三条并写明原因：拨备覆盖率 / 贷款拨备率（**区间**+分档条件，单侧判会误报）、
  流动性比例（人民币）（**无本体概念与观测**，只有 golden 锚）

## 6. 复现命令

```bash
cd fd-industry-data
export FD_CENTRAL_PG_DSN="postgresql://fd:<pwd>@100.64.0.3:30432/fd_open_data"
.venv/bin/python scripts/banking_alerts.py --dry-run --today 2026-11-08
.venv/bin/python scripts/banking_alerts.py --dry-run --today 2026-11-08 \
  --penalty-json /tmp/nfra_sample.json      # 用真实处罚样本验触发路径
.venv/bin/python -m pytest tests/test_alerts_core.py tests/test_banking_alerts.py -q
```

## 7. 未决

- 处罚源**尚未纳入平台排期**（`crawl_runs` 里仍无该源的运行记录）；目前只能手工从 cheap1 跑。
  要让它自动跑，需在调度侧允许 cheap1 出口（或加节点亲和）——属基建面，未动。
- `party_aliases` 现覆盖 15 家大行/股份行全称；城商行/农商行（如 600919 江苏、002142 宁波）
  只在部分文书出现，遇新主体应**补别名表**而不是放宽匹配规则。
- 阈值层 5/42 的覆盖要扩，属 B 线「比率抽取扩面」议题，不在本线。
