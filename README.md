# FD Industry Data Spiders

> **Wire (柏讯) product line** · the open-data supply line of [FindData](https://www.finddatatech.cloud/products/wire) — industry data spiders

Scrapling/HTTP 适配器构成的行业数据爬虫内容仓（fd-industry-data）：在役源清单以
`spiders/*/manifest.yaml` 为准（接入流程见 `docs/onboarding.md`），休眠与退役单元的
历史内容见 `archive/`。

## Spiders

| Spider | Source | Description |
|--------|--------|-------------|
| **flower-auction** | kifa.net | Kunming International Flower Auction Center 花拍数据（已按「需非云出口」parked，见 manifest 与 README） |

> 完整在役源以各目录 `manifest.yaml` 为准；休眠/退役单元（2026-10-04 批次等）见
> `archive/triage-cleanup-*` 的 INDEX。活跃的数据源侦察簿在 finddata 工作区
> `data-source-scouting/INDUSTRY-DATA-SOURCES.md`。

## Requirements

- Python 3.10+
- scrapling >= 0.4.7

Install dependencies:
```bash
pip install "scrapling[all]>=0.4.7"
scrapling install --force
```

## Usage

### Run Individual Spiders

```bash
# 平台约定的标准入口（内容通道调度即调用它）
python3 -c "import sys; sys.path.insert(0,'.'); from spiders.flower_auction.spider import run_flower_auction; print(run_flower_auction(limit=5))"

# 或按单元目录调试
cd spiders/flower-auction
python spider.py
```

### Fleet Baseline (All Scheduled Spiders)

Run every production source (manifest with a `schedule`) sequentially and
produce a success-rate baseline report:

```bash
python3 scripts/fleet_baseline.py --dry-run    # list scheduled sources only
python3 scripts/fleet_baseline.py --limit 20   # sequential run -> reports/fleet-baseline/
```

### Programmatic Usage

```python
from spiders.flower_auction.spider import FlowerAuctionSpider

spider = FlowerAuctionSpider()
result = spider.start()

print(f"Scraped {result.stats.items_scraped} items")
print(f"Duration: {result.stats.elapsed_seconds:.1f}s")

result.items.to_json("output.json", indent=True)
```

## Output Structure

Each spider produces:
- **SQLite database** in `spiders/<name>/data/`
- **JSON export** in `spiders/<name>/output/`

## Data Coverage

### Flower Industry Data
- Auction prices and trading volumes
- Flower variety statistics
- Market trends and seasonal data
- News and policy updates

### Open Data Platforms
- Dataset metadata extraction
- Download links and file information
- Dataset descriptions and tags
- Repository statistics

## Configuration

Each spider has its own `manifest.yaml` with:
- Data source URLs
- Output schema
- Rate limiting settings
- Feature flags
- `schedule` / `schedule_tz` — when it runs in production

### 为什么这些 manifest 留在这里，而不在部署仓

判据：**它描述的是代码行为还是集群编排**（openspec: `document-appset-boundary`）。

```
spiders/*/manifest.yaml   「这个源几点跑、传什么参数」 -> 代码属性 -> 跟代码走
部署仓的 ApplicationSet    「集群怎么把它渲染成 CronJob」 -> 集群编排 -> 跟集群走
```

搬走的后果不是「文件放错地方」：新增一个数据源要改两个仓，且两仓提交顺序错了会
渲染出大量失败的 CronJob——CronJob 的 initContainer 从 Gitee 取 `spiders/<name>/`
代码，manifest 与代码必须同版本。

**接入新数据源的完整改动面（只在本仓）**：

```
spiders/<name>/spider.py        爬取逻辑
spiders/<name>/manifest.yaml    排期与参数
developers/registry 等登记处     按 docs/onboarding.md
```

部署仓不需要任何改动——`fd-infra-deploy/argocd/industry-appset/applicationset.yaml`
的 git generator 会自动读到新的 `spiders/*/manifest.yaml`。

**例外**：`manifest.yaml` 里的 `image` 字段不生效，CronJob 用哪个 runner 版本由
`fd-infra-deploy` 的 ApplicationSet 模板统一指定（升级全部行业 runner = 改那一处）。

### Remote Browser (Playwright via CDP)

By default, browser-enabled spiders (flower-auction) use a local headless Chromium instance. To use a remote browser (e.g., on k8s), set `BROWSER_CDP_URL`:

```bash
# Local browser (default)
python spiders/flower-auction/spider.py

# Remote browser via CDP
BROWSER_CDP_URL=ws://browser-cdp:3000 python spiders/flower-auction/spider.py
```

The remote browser can be any CDP-compatible service (e.g., `browserless/chrome`, `zenika/alpine-chrome`, or a headless Chrome with `--remote-debugging-port=9222`). Each spider gets its own isolated browser context.

## Notes

- **`robots.txt` 检查有工具，但需按单元接入**：`fd_industry_data.http.allowed_by_robots(url)`
  按 host 缓存取 `robots.txt` 并与规则比对（标准库，零依赖）。**取不到时放行**
  （RFC 9309：没有 robots.txt 等于没有限制）。**没有单一收口**——spider 的取数方式分散在
  scrapling `Fetcher.get`（52 个）、`requests`、及各单元自有实现，工具无法在共享层强制生效；
  **新单元应当调用它**，既有单元按需迁移。
  （2026-11-09 之前这里写的是「All spiders respect `robots.txt`」——一句不成立的文档承诺，见审计 05 §6。）
- Rate limiting is configured to avoid overloading servers
- Flower auction data is time-sensitive and should be crawled frequently
- Kaggle/GitHub spiders focus on metadata, not full dataset downloads
- SQLite databases use deduplication constraints

## License

MIT

## Runtime: gitops 内容通道（gitops-crawl-runtime）

稳定运行时 = `finddata/fd-industry-runner` 镜像（Jenkins job `fd-industry-runner` 低频构建，含三段准入门：manifest v2 校验 → conformance gate → 镜像内 import 扫描，红则不推镜像）。内容 = 本仓 `spiders/<src>/`，经 chengsi ArgoCD 的 ApplicationSet（`fd-industry-crawl`）按 manifest v2 字段渲染 per-source CronJob。

**加源三步**：① `spiders/<src>/`（manifest.yaml + spider.py，入口 `run_<src>(limit)`）→ ② push 过 CI 准入门 → ③ manifest 补 `schedule`（如 `"23 3 * * *"`，可选 `memory_limit`/`cpu_limit`）即点亮；清空 schedule 或 `enabled: false` 即停用（prune）。无 schedule 的源静默，不产生任何运行。19 个存量坏源见 `scripts/quarantine.txt`（修复后从清单移除即受门禁约束）。

**遥测**：每次运行写中央表 `fd_open_data.crawl_runs`（source/kind/status/rows/commit/image_tag，成败皆报，报到失败本地兜底不伤主流程）；健康度卡片 `dashboards/crawl-health.html`（`python3 dashboards/crawl_health.py` 重生成）。特殊源 Job 用 `scripts/report_special_run.sh` 一行报到。

## 文档地图

`docs/` 下 44 篇 md 按主题分六组。先看这张表，再进具体文件。

**接入与流程**

| 文档 | 一句话 |
|---|---|
| `docs/onboarding.md` | 新数据源的完整接入流程（从侦察到上产） |
| `docs/onboarding-funnel.md` | 接入漏斗：每一档卡住多少源、为什么 |
| `docs/wave-extraction-cadence-2026-11-19.md` | 波抽取的节奏安排 |

**健康与巡检**

| 文档 | 一句话 |
|---|---|
| `docs/health-loop.md` | 健康巡检的完整设计（46K，最全的一份） |
| `docs/alert-sample-inventory-2026-11-08.md` | 告警样本清点 |
| `docs/silence-discipline-drift-2026-11-11.md` | 静默纪律的漂移复盘 |

**垂域口径**（每域一套：universe / series-curation / debt-ledger）

| 文档 | 一句话 |
|---|---|
| `docs/agriculture-universe.yaml` `docs/agriculture-universe-ratification.md` `docs/agriculture-series-curation.md` | 农业：口径、序列取舍 |
| `docs/hotel-kpi-caliber.md` `hotel-series-curation.md` `hotel-debt-ledger.md` | 酒店：KPI 口径与序列 |
| `docs/restaurant-segment-metrics.md` `restaurant-series-curation.md` `restaurant-debt-ledger.md` `restaurant-lane-mapping.md` | 餐饮 |
| `docs/flower-series-curation.md` `flower-debt-ledger.md` `flower-alerts-runbook.md` | 花卉 |
| `docs/hkex-results-headline-caliber.md` `hkex-segment-kpi-caliber.md` `samr-restaurant-caliber.md` `sec-edgar-tag-map.md` | 单一源的字段口径 |

**数据质量**（min-rows 系列，12 篇）

| 文档 | 一句话 |
|---|---|
| `docs/min-rows-acceptance-checklist.md` | 最少行数验收清单（先看这份） |
| `docs/min-rows-coverage-baseline.md` `min-rows-observation-baseline.md` | 各源基线 |
| `docs/min-rows-declaration-status.md` | 声明状态总表 |
| `docs/min-rows-withdrawn-states-plan.md` | 撤下状态的处置计划 |
| `docs/min-rows-calibration-analysis.md` `min-rows-observation-log.md` `min-rows-handoff.md` `min-rows-d-bucket-pending.md` | 标定、观察日志、交接 |

**运维与事故**

| 文档 | 一句话 |
|---|---|
| `docs/rollback/` | 回滚取证 |
| `docs/verification/` | 验证记录（按日期） |
| `docs/adr/` | 架构决策（自愈执行器归平台） |
| `docs/dual-remote-topology.md` | 双远端拓扑（GitHub 写、Gitee 镜像） |
| `docs/blocked-sources.md` | 被封/不可达的源清单 |
| `docs/limit-truncation-sweep-2026-11-11.md` `docs/pharma-scheduling-and-truncation-2026-11-11.md` | 截断问题的两次清扫 |
| `docs/writer-batch-insert-2026-11-11.md` | writer 批量写入改造 |
| `docs/zero-row-by-line.md` `docs/law-loader-zero-row-handoff.md` | 零行排查与交接 |
| `docs/release-drill-20261106.md` | 发布演练 |
| `docs/usaspending-awards-window-decision.md` `docs/agri-segment-caliber-notes.md` `docs/agri-pipeline-operations.md` `docs/agri-pilot-disclosure-rate.md` | 单一源的决策与数据 |

新增文档请同时更新本表——`fd-infra-deploy/tools/check_doc_map.py` 会核一致性。
