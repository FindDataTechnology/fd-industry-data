"""Shared runner for scrapling-based spiders (fd-open-data-protocol dispatch).

Each ``spiders/<src>/spider.py`` exposes a ``run_<src>(limit)`` entry point the
protocol dispatcher calls. They previously each copied the same ~20-line
fetch+extract loop; this centralizes it. A spider module now does:

    def run_agri_info(limit: int = 100) -> list[dict]:
        from fd_industry_data.runners import run_scrapling_spider
        return run_scrapling_spider(AgriinfoSpider(), START_URLS, limit=limit)
"""
from __future__ import annotations

import asyncio
from typing import Any


def run_scrapling_spider(spider: Any, start_urls: list[str], *, limit: int = 100) -> list[dict]:
    """Drive a scrapling ``Spider`` over ``start_urls``; return ≤ ``limit`` items.

    ``spider`` must expose ``extract_data(resp)`` and ``.logger`` (scrapling
    ``Spider`` does). Per-URL errors are logged and skipped; the loop stops once
    ``limit`` items are collected.
    """
    from scrapling.fetchers import FetcherSession

    items: list[dict] = []

    async def fetch_all():
        async with FetcherSession(impersonate="chrome120") as session:
            for url in start_urls:
                try:
                    resp = await session.get(url)
                    extracted = await spider.extract_data(resp)
                    if extracted:
                        items.extend(extracted if isinstance(extracted, list) else [extracted])
                        if len(items) >= limit:
                            break
                except Exception as e:
                    spider.logger.error(f"Error fetching {url}: {e}")
        return items[:limit]

    return asyncio.run(fetch_all())
