# Cleanup archive index — 休眠单元退役（runner 契约违契四单元）

Generated at: 2026-10-13
Change: 承接 `tabular-extraction-layer` 的参数通道审计（无独立 change；处置依据见
`scripts/runner-contract-dormant.txt` 与 `data-source-scouting/INDUSTRY-DATA-SOURCES.md`）
Evidence: 逐单元实测（源可达性 + 入口签名 + 与在役单元冗余关系）+ `check_manifest_commands` lint

处置总则（沿用 `archive/triage-cleanup-20261004/INDEX.md` 惯例）：休眠单元经实跑证据定性——
域死 / 入口坏死 / 政策停爬 / 无独有能力者归档；被运行时契约面引用者转规范保留。

| # | 原路径 | 处置 | 证据摘要 |
|---|---|---|---|
| 1 | `spiders/gtarsc` | 归档 | 站点自述「该域名已不再使用，请访问 data.csmar.com」；后继 CSMAR 为**付费订阅**学术库（不合「免 key」口径）。入口 `run_gtarsc(urls=None, save=True)` 不收 `limit` 且返回 True/False 而非 list[dict]（runner 点亮即 TypeError） |
| 2 | `spiders/hqcdc` | 归档 | `www.hqcdc.org.cn` 与裸域**均 DNS NXDOMAIN**（域已不存在）；入口同 #1 形态 |
| 3 | `spiders/intrinio` | 归档 | 站点存活但为**付费商业 API**（Pricing / Start Free Trial / Sign In）→ 按「需注册 key 的源不接」既有裁决不合规；入口同 #1 形态 |
| 4 | `spiders/isee` | 归档 | `www.isee.gov.cn` 与裸域**均 DNS NXDOMAIN**（域已不存在）；入口同 #1 形态 |

**四者共同形态（决定性证据）**：这四个**不是四个独立源适配器**，而是同一个「大杂烩」
通用爬虫原型**复制四份**——37 个域的 dispatch 列表**完全相同**，只有 `START_URLS` 不同；
52 个 `_extract_*` 方法中 **48 个是纯桩**（只取页面 `<title>` 与 meta description，
**不取任何数据**）；仅 ~4 个做表格抽取，且目标是 GitHub / UCI / FRED——这些**已有专门
在役单元**（`fred-data` 等）。37 域中 15 个已被在役单元覆盖（fred-data / nbs-stats / mof /
metal-com / cssn / cqvip / cap / bls / apipoer / cisia / caa / agri-info / zce …）。

**引用面体检（退役前置，已通过）**：`fd_industry_data/`（catalog.py / provider.py /
sites.yaml）、`k8s/`、`.github/`、`docs/`、`openspec/` **均无引用**；`scripts/` 命中仅本
登记表与 lint 文档字符串（已同步清理）；其余命中全在既有 `archive/` 内（历史归档自身）。
→ 无运行时契约面引用，退役不被否决。

**历史关联**：四个源的同域前身此前已分别退役过——`csmar`（gtarsc 域）、`autoflowassoc`
（hqcdc 域）、`intriniofinancialdata`（intrinio 域）、`envdata`（isee 域），均在
`archive/triage-cleanup-20260912/`。本次退役的是同名域的第二代「大杂烩」拷贝。
