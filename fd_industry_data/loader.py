"""Load a content spider module regardless of directory naming.

Source dirs mix `bls` and `yunnan-flowers` styles. Hyphenated dirs cannot be
imported as package submodules, so we try the package path first and fall
back to loading spider.py by file path under a unique module name. The
source dir is also prepended to sys.path so intra-dir imports keep working.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys


def load_spider_module(src: str, content_dir: str):
    """Import spiders/<src>/spider.py; return the module object."""
    us = src.replace("-", "_")
    src_dir = os.path.join(content_dir, src)
    if not os.path.isfile(os.path.join(src_dir, "spider.py")):
        raise FileNotFoundError(f"no spider.py under {src_dir}")

    for p in (os.path.dirname(os.path.abspath(content_dir)), src_dir):
        if p not in sys.path:
            sys.path.insert(0, p)
    sys.modules.pop("spider", None)  # stale sibling-import cache from other sources

    try:
        return importlib.import_module(f"spiders.{us}.spider")
    except ModuleNotFoundError:
        spec = importlib.util.spec_from_file_location(
            f"fd_content_{us}_spider", os.path.join(src_dir, "spider.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod
