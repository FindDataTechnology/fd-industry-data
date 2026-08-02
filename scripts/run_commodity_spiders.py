#!/usr/bin/env python3
"""
Run all Chinese commodity exchange spiders.
Executes SHFE, DCE, ZCE, and CISA spiders sequentially.
"""

import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime


async def run_spider(spider_name: str, spider_module: str):
    """Run a single spider"""
    print(f"\n{'='*60}")
    print(f"Running {spider_name} Spider")
    print(f"{'='*60}")
    
    try:
        # Add parent directory to path
        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        
        # Import the spider
        module = __import__(spider_module, fromlist=[""])
        spider_class_name = f"{spider_name}Spider"
        spider_class = getattr(module, spider_class_name)
        
        # Instantiate and run
        spider = spider_class()
        result = spider.start()
        
        print(f"\n✓ {spider_name} completed successfully")
        print(f"  Items scraped: {result.stats.items_scraped}")
        print(f"  Duration: {result.stats.elapsed_seconds:.1f}s")
        
        return {
            "name": spider_name,
            "status": "success",
            "items": result.stats.items_scraped,
            "duration": result.stats.elapsed_seconds,
        }
    
    except Exception as e:
        print(f"\n✗ {spider_name} failed: {e}")
        return {
            "name": spider_name,
            "status": "failed",
            "error": str(e),
        }


async def main():
    """Run all commodity spiders"""
    print("="*60)
    print("Chinese Commodity Exchange Spiders - Batch Runner")
    print("="*60)
    print(f"Started at: {datetime.now().isoformat()}")
    
    spiders = [
        ("SHFE", "spiders.shfe.spider"),
        ("DCE", "spiders.dce.spider"),
        ("ZCE", "spiders.zce.spider"),
        ("CISA", "spiders.cisa.spider"),
    ]
    
    results = []
    
    for spider_name, spider_module in spiders:
        result = await run_spider(spider_name, spider_module)
        results.append(result)
    
    # Summary
    print(f"\n{'='*60}")
    print("Execution Summary")
    print(f"{'='*60}")
    
    total_items = 0
    total_duration = 0
    successful = 0
    
    for result in results:
        if result["status"] == "success":
            successful += 1
            total_items += result["items"]
            total_duration += result["duration"]
            print(f"✓ {result['name']}: {result['items']} items in {result['duration']:.1f}s")
        else:
            print(f"✗ {result['name']}: {result['error']}")
    
    print(f"\n{'='*60}")
    print(f"Total: {successful}/{len(spiders)} spiders successful")
    print(f"Total items: {total_items}")
    print(f"Total duration: {total_duration:.1f}s")
    print(f"Completed at: {datetime.now().isoformat()}")
    print(f"{'='*60}")
    
    return 0 if successful == len(spiders) else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
