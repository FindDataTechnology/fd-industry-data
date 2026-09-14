#!/usr/bin/env python3
"""
Test script for metal and mining spiders.
Verifies all 4 spiders can be imported and have correct structure.
"""

import sys
from pathlib import Path

# Add spiers to path
spiers_dir = Path(__file__).parent
sys.path.insert(0, str(spiers_dir.parent))

def test_smm_metals():
    """Test SMM Metals spider."""
    print("\n" + "="*70)
    print("Testing SMM Metals Spider")
    print("="*70)
    
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "smm_metals_spider",
            spiers_dir / "smm-metals" / "spider.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        print(f"✓ Successfully loaded smm-metals/spider.py")
        print(f"  Metals: {list(module.METALS.keys())}")
        print(f"  Target URLs: {len(module.TARGET_URLS)}")
        print(f"  Function: get_smm_prices(metals=None)")
        
        # Test function signature
        import inspect
        sig = inspect.signature(module.get_smm_prices)
        print(f"  Parameters: {sig.parameters}")
        
        return True
    except Exception as e:
        print(f"✗ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cnia_lme():
    """Test CNIA-LME spider."""
    print("\n" + "="*70)
    print("Testing CNIA-LME Spider")
    print("="*70)
    
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cnia_lme_spider",
            spiers_dir / "cnia-lme" / "spider.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        print(f"✓ Successfully loaded cnia-lme/spider.py")
        print(f"  Metals: {list(module.METALS.keys())}")
        print(f"  Target URLs: {len(module.TARGET_URLS)}")
        print(f"  Function: get_lme_prices(metals=None)")
        
        import inspect
        sig = inspect.signature(module.get_lme_prices)
        print(f"  Parameters: {sig.parameters}")
        
        return True
    except Exception as e:
        print(f"✗ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_chinametal():
    """Test China Metal spider."""
    print("\n" + "="*70)
    print("Testing China Metal Spider")
    print("="*70)
    
    try:
        from chinametal.spider import get_chinametal_data, METALS, TARGET_URLS
        
        print(f"✓ Successfully imported chinametal.spider")
        print(f"  Metals: {list(METALS.keys())}")
        print(f"  Target URL categories: {list(TARGET_URLS.keys())}")
        print(f"  Function: get_chinametal_data(metals=None, include_news=True)")
        
        import inspect
        sig = inspect.signature(get_chinametal_data)
        print(f"  Parameters: {sig.parameters}")
        
        return True
    except Exception as e:
        print(f"✗ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cnia():
    """Test CNIA spider."""
    print("\n" + "="*70)
    print("Testing CNIA Spider")
    print("="*70)
    
    try:
        from cnia.spider import get_cnia_data, TARGET_URLS
        
        print(f"✓ Successfully imported cnia.spider")
        print(f"  Target URL categories: {list(TARGET_URLS.keys())}")
        print(f"  Function: get_cnia_data(include_stats=True, include_news=True, include_reports=True)")
        
        import inspect
        sig = inspect.signature(get_cnia_data)
        print(f"  Parameters: {sig.parameters}")
        
        return True
    except Exception as e:
        print(f"✗ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_manifests():
    """Test manifest files."""
    print("\n" + "="*70)
    print("Testing Manifest Files")
    print("="*70)
    
    manifests = [
        "smm-metals/manifest.yaml",
        "cnia-lme/manifest.yaml",
        "chinametal/manifest.yaml",
        "cnia/manifest.yaml",
    ]
    
    all_ok = True
    for manifest_path in manifests:
        full_path = spiers_dir / manifest_path
        if full_path.exists():
            print(f"✓ {manifest_path}")
        else:
            print(f"✗ {manifest_path} NOT FOUND")
            all_ok = False
    
    return all_ok


def test_readmes():
    """Test README files."""
    print("\n" + "="*70)
    print("Testing README Files")
    print("="*70)
    
    readmes = [
        "smm-metals/README.md",
        "cnia-lme/README.md",
        "chinametal/README.md",
        "cnia/README.md",
    ]
    
    all_ok = True
    for readme_path in readmes:
        full_path = spiers_dir / readme_path
        if full_path.exists():
            print(f"✓ {readme_path}")
        else:
            print(f"✗ {readme_path} NOT FOUND")
            all_ok = False
    
    return all_ok


def test_directories():
    """Test data and output directories."""
    print("\n" + "="*70)
    print("Testing Directory Structure")
    print("="*70)
    
    spiders = ["smm-metals", "cnia-lme", "chinametal", "cnia"]
    
    all_ok = True
    for spider in spiders:
        data_dir = spiers_dir / spider / "data"
        output_dir = spiers_dir / spider / "output"
        
        if data_dir.exists():
            print(f"✓ {spider}/data/")
        else:
            print(f"✗ {spider}/data/ NOT FOUND")
            all_ok = False
        
        if output_dir.exists():
            print(f"✓ {spider}/output/")
        else:
            print(f"✗ {spider}/output/ NOT FOUND")
            all_ok = False
    
    return all_ok


def main():
    """Run all tests."""
    print("\n" + "="*70)
    print("METAL & MINING SPIDERS - VERIFICATION TEST")
    print("="*70)
    
    results = {
        "SMM Metals": test_smm_metals(),
        "CNIA-LME": test_cnia_lme(),
        "China Metal": test_chinametal(),
        "CNIA": test_cnia(),
        "Manifests": test_manifests(),
        "READMEs": test_readmes(),
        "Directories": test_directories(),
    }
    
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status:8s} | {test_name}")
    
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    
    print("\n" + "="*70)
    print(f"Results: {passed}/{total} tests passed")
    print("="*70)
    
    if passed == total:
        print("\n✓ All tests passed! Spiders are ready to use.")
        print("\nNext steps:")
        print("  1. Run individual spiders: cd spiers/<spider-name> && python spider.py")
        print("  2. Check METAL_MINING_SPIDERS_COMPLETE.md for usage guide")
        print("  3. Review individual README.md files for details")
        return 0
    else:
        print("\n✗ Some tests failed. Please check the errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
