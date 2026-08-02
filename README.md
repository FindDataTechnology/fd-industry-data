# FD Industry Data Spiders

Scrapling-based spiders for collecting flower industry data and open dataset metadata from Chinese and international sources.

## Spiders

| Spider | Source | Description |
|--------|--------|-------------|
| **flower-association** | chinaflower.org.cn | China Flower Association news, policies, market data |
| **flower-trading** | kunmingflower.com | Kunming Flower Trading Center prices and varieties |
| **flower-auction** | kifc.cn | Kunming International Flower Auction Center auction data |
| **kaggle** | kaggle.com/datasets | Kaggle dataset metadata (flower, China, agriculture) |
| **github-datasets** | github.com | GitHub awesome China dataset repositories |

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
# China Flower Association
cd spiders/flower-association
python spider.py

# Kunming Flower Trading Center
cd spiders/flower-trading
python spider.py

# Kunming International Flower Auction Center
cd spiders/flower-auction
python spider.py

# Kaggle Datasets
cd spiders/kaggle
python spider.py

# GitHub Datasets
cd spiders/github-datasets
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

By default, browser-enabled spiders (flower-auction, flower-trading) use a local headless Chromium instance. To use a remote browser (e.g., on k8s), set `BROWSER_CDP_URL`:

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
