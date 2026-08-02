#!/usr/bin/env python3
"""
Test script for Chinese media, social, and open data spiders.

Tests import and basic structure of all 6 spiders:
1. People's Daily Data Center (spiers/people-daily)
2. WeChat Official Account Platform (spiers/wechat-mp)
3. Weibo Open Platform (spiers/weibo-open)
4. Toutiao Open Platform (spiers/toutiao-open)
5. Kaggle Datasets (spiders/kaggle)
6. GitHub Awesome China Dataset (spiders/github-datasets)
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

SPIDERS = [
    {
        "name": "people-daily",
        "label": "People's Daily Data Center (人民日报数据中心)",
        "module_path": "spiers.people-daily.spider",
        "class_name": "PeopleDailySpider",
        "dir": BASE / "spiers" / "people-daily",
    },
    {
        "name": "wechat-mp",
        "label": "WeChat Official Account Platform (微信公众号平台)",
        "module_path": "spiers.wechat-mp.spider",
        "class_name": "WeChatMPSpider",
        "dir": BASE / "spiers" / "wechat-mp",
    },
    {
        "name": "weibo-open",
        "label": "Weibo Open Platform (微博开放平台)",
        "module_path": "spiers.weibo-open.spider",
        "class_name": "WeiboOpenSpider",
        "dir": BASE / "spiers" / "weibo-open",
    },
    {
        "name": "toutiao-open",
        "label": "Toutiao Open Platform (今日头条开放平台)",
        "module_path": "spiers.toutiao-open.spider",
        "class_name": "ToutiaoOpenSpider",
        "dir": BASE / "spiers" / "toutiao-open",
    },
    {
        "name": "kaggle",
        "label": "Kaggle Datasets",
        "module_path": "spiders.kaggle.spider",
        "class_name": "KaggleDatasetsSpider",
        "dir": BASE / "spiders" / "kaggle",
    },
    {
        "name": "github-datasets",
        "label": "GitHub Awesome China Dataset",
        "module_path": "spiders.github-datasets.spider",
        "class_name": "GithubDatasetsSpider",
        "dir": BASE / "spiders" / "github-datasets",
    },
]


def check_files(spider: dict) -> list[str]:
    errors = []
    d = spider["dir"]
    if not (d / "spider.py").exists():
        errors.append("spider.py missing")
    if not (d / "manifest.yaml").exists():
        errors.append("manifest.yaml missing")
    if not (d / "README.md").exists():
        errors.append("README.md missing")
    return errors


def check_import(spider: dict) -> str | None:
    try:
        spec = importlib.util.spec_from_file_location(
            f"test_{spider['name']}",
            spider["dir"] / "spider.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls = getattr(mod, spider["class_name"], None)
        if cls is None:
            return f"class {spider['class_name']} not found"
        if not hasattr(cls, "name"):
            return "missing spider name"
        if not hasattr(cls, "start_urls"):
            return "missing start_urls"
        return None
    except Exception as e:
        return str(e)


def main():
    print(f"{'=' * 70}")
    print("Chinese Media & Open Data Spiders - Test Suite")
    print(f"{'=' * 70}\n")

    passed = 0
    failed = 0

    for spider in SPIDERS:
        print(f"[{spider['name']}] {spider['label']}")

        file_errors = check_files(spider)
        if file_errors:
            for err in file_errors:
                print(f"  FAIL: {err}")
            failed += 1
            continue

        import_error = check_import(spider)
        if import_error:
            print(f"  FAIL: import error - {import_error}")
            failed += 1
            continue

        print(f"  OK: files present, class importable")
        passed += 1

    print(f"\n{'=' * 70}")
    print(f"Results: {passed} passed, {failed} failed, {len(SPIDERS)} total")
    print(f"{'=' * 70}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
