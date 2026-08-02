#!/usr/bin/env python3
"""
Final verification script for Chinese commodity exchange spiders.
Checks all files, directories, and configurations.
"""

import os
import sys
from pathlib import Path


def check_directory_structure():
    """Verify directory structure"""
    print("Checking directory structure...")
    
    base_dir = Path(__file__).parent.parent
    spider_dirs = ["shfe", "dce", "zce", "cisa"]
    
    all_good = True
    
    for spider_name in spider_dirs:
        spider_path = base_dir / "spiders" / spider_name
        
        # Check directory exists
        if not spider_path.exists():
            print(f"✗ {spider_name.upper()} directory not found")
            all_good = False
            continue
        
        # Check required files
        required_files = [
            "spider.py",
            "manifest.yaml",
            "README.md",
            ".gitignore",
        ]
        
        for file_name in required_files:
            file_path = spider_path / file_name
            if not file_path.exists():
                print(f"✗ {spider_name.upper()}/{file_name} not found")
                all_good = False
        
        # Check directories
        required_dirs = ["data", "output"]
        for dir_name in required_dirs:
            dir_path = spider_path / dir_name
            if not dir_path.exists():
                print(f"✗ {spider_name.upper()}/{dir_name}/ directory not found")
                all_good = False
        
        if all_good:
            print(f"✓ {spider_name.upper()} structure complete")
    
    return all_good


def check_scripts():
    """Verify scripts exist"""
    print("\nChecking scripts...")
    
    base_dir = Path(__file__).parent.parent
    scripts = [
        "test_commodity_spiders.py",
        "run_commodity_spiders.py",
    ]
    
    all_good = True
    
    for script_name in scripts:
        script_path = base_dir / "scripts" / script_name
        if not script_path.exists():
            print(f"✗ scripts/{script_name} not found")
            all_good = False
        else:
            print(f"✓ scripts/{script_name} exists")
    
    return all_good


def check_documentation():
    """Verify documentation files"""
    print("\nChecking documentation...")
    
    base_dir = Path(__file__).parent.parent
    docs = [
        "COMMODITY_SPIDERS_COMPLETE.md",
        "COMMODITY_DATA_DICTIONARY.md",
        "GETTING_STARTED_COMMODITY.md",
        "IMPLEMENTATION_SUMMARY.md",
    ]
    
    all_good = True
    
    for doc_name in docs:
        doc_path = base_dir / doc_name
        if not doc_path.exists():
            print(f"✗ {doc_name} not found")
            all_good = False
        else:
            size = doc_path.stat().st_size
            print(f"✓ {doc_name} exists ({size:,} bytes)")
    
    return all_good


def check_spider_imports():
    """Verify spiders can be imported"""
    print("\nChecking spider imports...")
    
    base_dir = Path(__file__).parent.parent
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    
    spiders = {
        "SHFE": "spiders.shfe.spider",
        "DCE": "spiders.dce.spider",
        "ZCE": "spiders.zce.spider",
        "CISA": "spiders.cisa.spider",
    }
    
    all_good = True
    
    for name, module_path in spiders.items():
        try:
            module = __import__(module_path, fromlist=[""])
            spider_class_name = f"{name}Spider"
            spider_class = getattr(module, spider_class_name)
            spider = spider_class()
            print(f"✓ {name} spider imports successfully")
        except Exception as e:
            print(f"✗ {name} spider import failed: {e}")
            all_good = False
    
    return all_good


def check_manifests():
    """Verify manifest files are valid YAML"""
    print("\nChecking manifest files...")
    
    base_dir = Path(__file__).parent.parent
    spider_dirs = ["shfe", "dce", "zce", "cisa"]
    
    all_good = True
    
    try:
        import yaml
    except ImportError:
        print("⚠ PyYAML not installed, skipping manifest validation")
        return True
    
    for spider_name in spider_dirs:
        manifest_path = base_dir / "spiders" / spider_name / "manifest.yaml"
        
        try:
            with open(manifest_path, "r") as f:
                manifest = yaml.safe_load(f)
            
            # Check required fields
            required_fields = ["name", "version", "description", "source", "spider"]
            missing = [f for f in required_fields if f not in manifest]
            
            if missing:
                print(f"✗ {spider_name.upper()} manifest missing: {missing}")
                all_good = False
            else:
                print(f"✓ {spider_name.upper()} manifest valid")
        
        except Exception as e:
            print(f"✗ {spider_name.upper()} manifest validation failed: {e}")
            all_good = False
    
    return all_good


def main():
    """Run all verification checks"""
    print("="*60)
    print("Chinese Commodity Exchange Spiders - Final Verification")
    print("="*60)
    
    results = []
    
    # Check 1: Directory structure
    results.append(("Directory Structure", check_directory_structure()))
    
    # Check 2: Scripts
    results.append(("Scripts", check_scripts()))
    
    # Check 3: Documentation
    results.append(("Documentation", check_documentation()))
    
    # Check 4: Spider imports
    results.append(("Spider Imports", check_spider_imports()))
    
    # Check 5: Manifests
    results.append(("Manifests", check_manifests()))
    
    # Summary
    print("\n" + "="*60)
    print("Verification Summary")
    print("="*60)
    
    total_checks = len(results)
    passed_checks = sum(1 for _, result in results if result)
    
    for check_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {check_name}")
    
    print(f"\n{'='*60}")
    print(f"Total: {passed_checks}/{total_checks} checks passed")
    print(f"{'='*60}")
    
    if passed_checks == total_checks:
        print("\n✅ All verification checks passed!")
        print("Spiders are ready for production use.")
        return 0
    else:
        print("\n❌ Some verification checks failed.")
        print("Please review the errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
