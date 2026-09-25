"""Spider skeleton for a new source. Copy this directory to
spiders/<my-source>/ and rename run_template_source / the class to match
the directory name (hyphens become underscores in the entry point)."""
from __future__ import annotations

from fd_industry_data.runners import run_scrapling_spider

START_URLS = [
    "https://example.com/list",
]


class MySourceSpider:
    # any scrapling Spider works; a plain class with extract_data + logger also does
    def __init__(self):
        import logging

        self.logger = logging.getLogger("my-source")

    async def extract_data(self, resp) -> list[dict]:
        items = []
        for node in resp.css("div.record") or []:
            items.append({
                "title": (node.css_first("h3::text") or "").strip(),
                "url": node.attrib.get("href", ""),
                "source": "my-source",
            })
        return items


def run_template_source(limit: int = 100) -> list[dict]:
    return run_scrapling_spider(MySourceSpider(), START_URLS, limit=limit)
