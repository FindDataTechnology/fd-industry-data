#!/usr/bin/env python3
"""
Test script for Chinese commodity exchange spiders.
Tests SHFE, DCE, ZCE, and CISA spiders.
"""

import asyncio
import sys
import os
from pathlib import Path


def test_spider_imports():
    """Test that all spiders can be imported"""
    print("Testing spider imports...")
    
    spiders = {
        "SHFE": "spiders.shfe.spider",
        "DCE": "spiders.dce.spider",
        "ZCE": "spiders.zce.spider",
        "CISA": "spiders.cisa.spider",
    }
    
    results = {}
    
    for name, module_path in spiders.items():
        try:
            # Add parent directory to path
            parent_dir = str(Path(__file__).parent.parent)
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            
            # Import the module
            module = __import__(module_path, fromlist=[""])
            
            # Get the spider class
            spider_class_name = f"{name}Spider"
            spider_class = getattr(module, spider_class_name)
            
            # Instantiate
            spider = spider_class()
            
            print(f"✓ {name} spider imported successfully")
            results[name] = {"status": "success", "class": spider_class.__name__}
            
        except Exception as e:
            print(f"✗ {name} spider import failed: {e}")
            results[name] = {"status": "failed", "error": str(e)}
    
    return results


def test_spider_attributes():
    """Test that spiders have required attributes"""
    print("\nTesting spider attributes...")
    
    spiders_to_test = [
        ("SHFE", "spiders.shfe.spider", "SHFESpider"),
        ("DCE", "spiders.dce.spider", "DCESpider"),
        ("ZCE", "spiders.zce.spider", "ZCESpider"),
        ("CISA", "spiders.cisa.spider", "CISASpider"),
    ]
    
    results = {}
    
    for name, module_path, class_name in spiders_to_test:
        try:
            module = __import__(module_path, fromlist=[""])
            spider_class = getattr(module, class_name)
            spider = spider_class()
            
            # Check required attributes
            required_attrs = [
                "name",
                "start_urls",
                "concurrent_requests",
                "download_delay",
                "robots_txt_obey",
            ]
            
            missing = []
            for attr in required_attrs:
                if not hasattr(spider, attr):
                    missing.append(attr)
            
            if missing:
                print(f"✗ {name} spider missing attributes: {missing}")
                results[name] = {"status": "failed", "missing": missing}
            else:
                print(f"✓ {name} spider has all required attributes")
                results[name] = {
                    "status": "success",
                    "name": spider.name,
                    "start_urls_count": len(spider.start_urls) if isinstance(spider.start_urls, list) else len(list(spider.start_urls.values())[0]) if isinstance(spider.start_urls, dict) else 0,
                }
        
        except Exception as e:
            print(f"✗ {name} spider attribute test failed: {e}")
            results[name] = {"status": "failed", "error": str(e)}
    
    return results


def test_manifest_files():
    """Test that manifest.yaml files exist and are valid"""
    print("\nTesting manifest files...")
    
    spider_dirs = ["shfe", "dce", "zce", "cisa"]
    results = {}
    
    for spider_name in spider_dirs:
        manifest_path = Path(__file__).parent.parent / "spiders" / spider_name / "manifest.yaml"
        
        if not manifest_path.exists():
            print(f"✗ {spider_name.upper()} manifest.yaml not found")
            results[spider_name.upper()] = {"status": "failed", "error": "File not found"}
            continue
        
        try:
            import yaml
            with open(manifest_path, "r") as f:
                manifest = yaml.safe_load(f)
            
            # Check required fields
            required_fields = ["name", "version", "description", "source", "spider"]
            missing = [f for f in required_fields if f not in manifest]
            
            if missing:
                print(f"✗ {spider_name.upper()} manifest missing fields: {missing}")
                results[spider_name.upper()] = {"status": "failed", "missing": missing}
            else:
                print(f"✓ {spider_name.upper()} manifest.yaml is valid")
                results[spider_name.upper()] = {
                    "status": "success",
                    "name": manifest["name"],
                    "version": manifest["version"],
                }
        
        except Exception as e:
            print(f"✗ {spider_name.upper()} manifest validation failed: {e}")
            results[spider_name.upper()] = {"status": "failed", "error": str(e)}
    
    return results


def test_readme_files():
    """Test that README.md files exist"""
    print("\nTesting README files...")
    
    spider_dirs = ["shfe", "dce", "zce", "cisa"]
    results = {}
    
    for spider_name in spider_dirs:
        readme_path = Path(__file__).parent.parent / "spiders" / spider_name / "README.md"
        
        if not readme_path.exists():
            print(f"✗ {spider_name.upper()} README.md not found")
            results[spider_name.upper()] = {"status": "failed", "error": "File not found"}
        else:
            print(f"✓ {spider_name.upper()} README.md exists")
            results[spider_name.upper()] = {"status": "success", "size": readme_path.stat().st_size}
    
    return results


def main():
    """Run all tests"""
    print("=" * 60)
    print("Chinese Commodity Exchange Spiders - Test Suite")
    print("=" * 60)
    
    all_results = {}
    
    # Test 1: Imports
    all_results["imports"] = test_spider_imports()
    
    # Test 2: Attributes
    all_results["attributes"] = test_spider_attributes()
    
    # Test 3: Manifests
    all_results["manifests"] = test_manifest_files()
    
    # Test 4: READMEs
    all_results["readmes"] = test_readme_files()
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    
    total_tests = 0
    passed_tests = 0
    
    for test_name, test_results in all_results.items():
        print(f"\n{test_name.upper()}:")
        for spider_name, result in test_results.items():
            total_tests += 1
            if result["status"] == "success":
                passed_tests += 1
                print(f"  ✓ {spider_name}")
            else:
                print(f"  ✗ {spider_name}: {result.get('error', 'Unknown error')}")
    
    print(f"\n{'='*60}")
    print(f"Total: {passed_tests}/{total_tests} tests passed")
    print(f"{'='*60}")
    
    return 0 if passed_tests == total_tests else 1


if __name__ == "__main__":
    sys.exit(main())
