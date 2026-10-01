"""nifdc-batchcert — 中检院生物制品批签发公示数据.

Source: 中国药品检定研究院 (NIFDC/NIDC) 生物制品批签发查询系统.
https://bio.nifdc.org.cn/pqf/ 301-redirects to https://bio.nidc.org.cn/pqf/
(the institute was renamed, the old domain forwards at nginx level).

Live data paths (measured 2026-10-02):
  1. GET  search.do?formAction=pqfGs
        -> weekly public-notice summary, one link per week:
           search.do?formAction=listGsxq&parameter1=<id>&parameter2=<enc date range>
           (~39 distinct weeks, 2025-09 .. 2026-10; page charset GBK)
  2. GET  the listGsxq URL of a week (charset UTF-8)
        -> 8-column table: 序号/产品名称/批号/有效期至/上市许可持有人/
           证书编号/签发结论/批签发机构

NOTE (brief deviation): the brief's 13-field by-product-name search
(`POST search.do?formAction=list1`, fields entNameS/drugNameS/codePiS)
is server-side empty on the live host — every query, including an empty
one, returns 搜到0条结果. The working surfaces (weekly notices and the
certificate-number search `list2`) expose 8 columns; per the brief's
"以实测表头为准" clause this spider implements the measured 8-column
schema. Product-name lookup is provided as a client-side filter
(product_name parameter) over the fetched rows instead of the dead
list1 endpoint.

No auth, no captcha. TLS cert is CFCA OV (CN=*.nidc.org.cn); verify=False
per brief so the fetch works regardless of the local trust store.
Domestic source: direct connection only, never proxied.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from lxml import html as lxml_html
from scrapling.fetchers import FetcherSession

logger = logging.getLogger("nifdc-batchcert")

PQF_BASE = "https://bio.nidc.org.cn/pqf/"
SUMMARY_URL = PQF_BASE + "search.do?formAction=pqfGs"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_WEEK_DATE_RE = re.compile(
    r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)

# column order on the listGsxq table (measured header, 8 columns)
_WEEK_HEADERS = (
    "序号", "产品名称", "批号", "有效期至",
    "上市许可持有人", "证书编号", "签发结论", "批签发机构",
)


def _decode(body: bytes) -> str:
    """pqfGs is GBK, listGsxq pages are UTF-8 — sniff defensively."""
    for enc in ("utf-8", "gb18030"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _parse_week_links(page_html: str) -> list[dict]:
    """Extract the weekly listGsxq links from the pqfGs summary page.

    Dedupes by href, keeps the site's order, sorts newest week first
    (weeks with an unparsable label sort last).
    """
    doc = lxml_html.fromstring(page_html)
    weeks: dict[str, dict] = {}
    for a in doc.xpath('//a[contains(@href, "formAction=listGsxq")]'):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        label = _clean(a.text_content())
        m = _WEEK_DATE_RE.search(label)
        # (year, month, day) of the window start, newest first;
        # unparsable labels go last (0 sorts below any real year)
        sort_key = (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)
        weeks[href] = {
            "url": urljoin(PQF_BASE, href),
            "label": label,
            "sort_key": sort_key,
        }
    out = sorted(weeks.values(), key=lambda w: w["sort_key"], reverse=True)
    logger.info("pqfGs: %d distinct week links", len(out))
    return out


def _parse_week_rows(page_html: str, week_label: str, source_url: str) -> list[dict]:
    """Parse the 8-column batch-release table of one weekly notice page."""
    doc = lxml_html.fromstring(page_html)
    rows: list[dict] = []
    scraped_at = datetime.now(timezone.utc).isoformat()
    for tr in doc.xpath("//tr"):
        tds = tr.xpath("./td")
        if len(tds) != len(_WEEK_HEADERS):
            continue
        cells = [_clean(td.text_content()) for td in tds]
        if cells[0] == _WEEK_HEADERS[0]:  # header row
            continue
        if not cells[0].isdigit():  # 序号 must be numeric on data rows
            continue
        # 解析失败留空不写脏行: require the identity fields
        if not cells[1] or not cells[2]:
            continue
        rows.append(
            {
                "seq_no": cells[0],
                "product_name": cells[1],
                "batch_no": cells[2],
                "expire_date": cells[3],
                "license_holder": cells[4],
                "cert_no": cells[5],
                "issue_conclusion": cells[6],
                "issuing_agency": cells[7],
                "issue_date_window": week_label,
                "scraped_at": scraped_at,
                "source_url": source_url,
            }
        )
    return rows


async def _collect(limit: int, product_name: str) -> list[dict]:
    fetched = 0  # week pages fetched (observability)
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()

    async with FetcherSession(
        impersonate="chrome120",
        timeout=25,
        verify=False,   # CFCA cert, treated as untrusted per brief
        retries=1,      # exactly one attempt — no retry storms
    ) as session:
        resp = await session.get(
            SUMMARY_URL, headers={"User-Agent": USER_AGENT}
        )
        if resp.status != 200:
            logger.warning("pqfGs status=%s — log-and-skip", resp.status)
            return []
        weeks = _parse_week_links(_decode(resp.body))
        if not weeks:
            logger.warning("pqfGs: no week links found — log-and-skip")
            return []

        needle = product_name.strip()
        for week in weeks:
            if len(rows) >= limit:
                break
            try:
                wresp = await session.get(
                    week["url"], headers={"User-Agent": USER_AGENT}
                )
            except Exception as e:  # noqa: BLE001 — per-week log-and-skip
                logger.warning("week fetch failed %s: %s — skip", week["url"], e)
                continue
            fetched += 1
            if wresp.status != 200:
                logger.warning(
                    "week status=%s %s — log-and-skip", wresp.status, week["url"]
                )
                continue
            for row in _parse_week_rows(
                _decode(wresp.body), week["label"], week["url"]
            ):
                if needle and needle not in row["product_name"]:
                    continue
                key = (row["cert_no"], row["batch_no"])
                if key in seen:
                    continue
                seen.add(key)
                if len(rows) < limit:
                    rows.append(row)
        logger.info(
            "nifdc-batchcert: %d rows from %d week pages (weeks available: %d)",
            len(rows), fetched, len(weeks),
        )
    return rows


def run_nifdc_batchcert(limit: int = 100, product_name: str = "") -> list[dict]:
    """Entry point for fd-runner (spiders/nifdc-batchcert).

    Args:
        limit: maximum rows to return (<= limit enforced).
        product_name: optional substring filter on 产品名称 (client-side;
            the server-side by-name search endpoint is empty, see module
            docstring).

    Returns:
        list of batch-release row dicts (schema = manifest columns).
    """
    from fd_industry_data.cancel_event import is_set as cancel_set

    async def _run() -> list[dict]:
        if cancel_set():
            logger.info("cancel requested before start; returning empty")
            return []
        return await _collect(max(int(limit), 0), product_name or "")

    return asyncio.run(_run())[: max(int(limit), 0)]


if __name__ == "__main__":
    for r in run_nifdc_batchcert(limit=5):
        print(r)
