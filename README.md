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

### Run All Spiders

```bash
python run_all.py
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

- All spiders respect `robots.txt`
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
