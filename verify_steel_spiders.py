#!/usr/bin/env python3
"""Verify all steel industry spiders are properly set up."""

import sys
from pathlib import Path
import re

BASE_DIR = Path(__file__).parent.resolve()

def check_file(path, desc):
    """Check if file exists and is readable."""
    exists = path.exists() and path.is_file()
    status = "OK" if exists else "MISSING"
    size = f" ({path.stat().st_size} bytes)" if exists else ""
    print(f"  [{status}] {desc}: {path}{size}")
    return exists

def main():
    print("=" * 70)
    print("Steel Industry Spiders Verification")
    print("=" * 70)
    print()
    
    categories = [
        ("steel-assoc", "Steel Industry Associations"),
        ("steel-exchange", "Steel Exchanges & Futures"),
        ("steel-statistics", "Statistical Office Websites"),
        ("steel-info", "Industry Information Networks"),
        ("steel-market", "Market Data Platforms"),
    ]
    
    total_checks = 0
    passed_checks = 0
    
    for cat_slug, cat_name in categories:
        cat_dir = BASE_DIR / "spiers" / cat_slug
        
        print(f"[{cat_name}]")
        print("-" * 70)
        
        # Check spider file
        total_checks += 1
        if check_file(cat_dir / "spider.py", "Spider script"):
            passed_checks += 1
        
        # Check data directory
        total_checks += 1
        if (cat_dir / "data").is_dir():
            print(f"  [OK] Data directory: {cat_dir}/data/")
            passed_checks += 1
        else:
            print(f"  [MISSING] Data directory")
        
        # Check output directory
        total_checks += 1
        if (cat_dir / "output").is_dir():
            print(f"  [OK] Output directory: {cat_dir}/output/")
            passed_checks += 1
        else:
            print(f"  [MISSING] Output directory")
        
        # Check README
        total_checks += 1
        if check_file(cat_dir / "README.md", "README"):
            passed_checks += 1
        
        # Count URLs in spider
        spider_text = (cat_dir / "spider.py").read_text() if (cat_dir / "spider.py").exists() else ""
        urls = re.findall(r'(https?://[^\s"\']+)', spider_text)
        unique_urls = len(set(urls))
        print(f"  [INFO] Detected URLs in spider: {unique_urls}")
        
        print()
    
    # Check manifests
    print("[Manifest Files]")
    print("-" * 70)
    manifest_files = [
        "manifests/steel-association.yaml",
        "manifests/steel-exchange.yaml", 
        "manifests/steel-statistics.yaml",
        "manifests/steel-information.yaml",
        "manifests/steel-market.yaml",
    ]
    
    for mf in manifest_files:
        total_checks += 1
        if check_file(BASE_DIR / mf, f"Manifest: {mf.split('/')[-1]}"):
            passed_checks += 1
    
    print()
    
    # Summary
    print("=" * 70)
    print(f"Verification Results: {passed_checks}/{total_checks} checks passed")
    print("=" * 70)
    
    if passed_checks == total_checks:
        print("\n[SUCCESS] All systems ready!")
        print("\nNext steps:")
        print("  1. Implement custom parsing logic in each spider.py")
        print("  2. Test with single URL first")
        print("  3. Run full extraction once verified")
        return 0
    else:
        failed = total_checks - passed_checks
        print(f"\n[WARN] {failed} check(s) failed. Review missing files above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
