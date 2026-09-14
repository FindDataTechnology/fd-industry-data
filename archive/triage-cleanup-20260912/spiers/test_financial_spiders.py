#!/usr/bin/env python3
"""
Financial Platform Spiders Verification Script
Tests all 5 financial data platform spiders for proper structure and imports.
"""

import sys
from pathlib import Path

def test_spider_structure(spider_dir: str) -> dict:
    """Test spider directory structure."""
    base = Path(spider_dir)
    results = {
        "directory_exists": base.exists(),
        "spider_py": False,
        "manifest_yaml": False,
        "readme_md": False,
        "data_dir": False,
        "output_dir": False,
    }
    
    if not results["directory_exists"]:
        return results
    
    results["spider_py"] = (base / "spider.py").exists()
    results["manifest_yaml"] = (base / "manifest.yaml").exists()
    results["readme_md"] = (base / "README.md").exists()
    results["data_dir"] = (base / "data").exists()
    results["output_dir"] = (base / "output").exists()
    
    return results


def test_spider_import(spider_dir: str) -> bool:
    """Test if spider can be imported."""
    try:
        spider_path = Path(spider_dir) / "spider.py"
        if not spider_path.exists():
            return False
        
        with open(spider_path) as f:
            code = f.read()
        
        # Check for syntax errors
        compile(code, spider_path, "exec")
        return True
    except Exception as e:
        print(f"  Import error: {e}")
        return False


def main():
    spiders = [
        ("Wind Financial", "wind-financial"),
        ("iFinD", "ifind-5ifin"),
        ("Eastmoney Choice", "eastmoney-choice"),
        ("MySteel", "mysteel"),
        ("SMM Metals", "smm-metals"),
    ]
    
    print("=" * 70)
    print("Financial Platform Spiders Verification")
    print("=" * 70)
    print()
    
    all_passed = True
    
    for name, directory in spiders:
        print(f"Testing: {name} ({directory})")
        
        # Test structure
        structure = test_spider_structure(directory)
        
        if not structure["directory_exists"]:
            print(f"  ❌ Directory not found")
            all_passed = False
            continue
        
        # Check files
        files_ok = all([
            structure["spider_py"],
            structure["manifest_yaml"],
            structure["readme_md"],
            structure["data_dir"],
            structure["output_dir"],
        ])
        
        if files_ok:
            print(f"  ✅ Structure: OK")
        else:
            print(f"  ❌ Structure: Missing files")
            for key, value in structure.items():
                if key != "directory_exists" and not value:
                    print(f"     - Missing: {key}")
            all_passed = False
        
        # Test import
        if test_spider_import(directory):
            print(f"  ✅ Import: OK")
        else:
            print(f"  ❌ Import: Failed")
            all_passed = False
        
        print()
    
    print("=" * 70)
    if all_passed:
        print("✅ All tests passed!")
        print("=" * 70)
        return 0
    else:
        print("❌ Some tests failed")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(main())
