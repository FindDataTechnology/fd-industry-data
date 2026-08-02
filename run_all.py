import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "spiders", "flower-association"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "spiders", "flower-trading"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "spiders", "flower-auction"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "spiders", "kaggle"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "spiders", "github-datasets"))

from scrapling.spiders import Spider


def run_spider(spider_class, name):
    print(f"\n{'='*60}")
    print(f"Running: {name}")
    print(f"{'='*60}")
    try:
        spider = spider_class()
        result = spider.start()
        print(f"  Items scraped: {result.stats.items_scraped}")
        print(f"  Requests: {result.stats.requests_count}")
        print(f"  Duration: {result.stats.elapsed_seconds:.1f}s")
        print(f"  Completed: {result.completed}")
        return result.stats.items_scraped
    except Exception as e:
        print(f"  ERROR: {e}")
        return 0


def main():
    from spider import FlowerAssociationSpider
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "spiders", "flower-trading"))
    from spider import FlowerTradingSpider
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "spiders", "flower-auction"))
    from spider import FlowerAuctionSpider
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "spiders", "kaggle"))
    from spider import KaggleDatasetsSpider
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "spiders", "github-datasets"))
    from spider import GithubDatasetsSpider

    spiders = [
        (FlowerAssociationSpider, "China Flower Association"),
        (FlowerTradingSpider, "Kunming Flower Trading Center"),
        (FlowerAuctionSpider, "Kunming International Flower Auction Center"),
        (KaggleDatasetsSpider, "Kaggle Datasets"),
        (GithubDatasetsSpider, "GitHub Awesome China Datasets"),
    ]

    total_items = 0
    for spider_class, name in spiders:
        total_items += run_spider(spider_class, name)

    print(f"\n{'='*60}")
    print(f"Total items scraped: {total_items}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
