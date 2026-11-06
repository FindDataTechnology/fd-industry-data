"""spiders/education_oer_aggregators —— OER 聚合器层（2026-11-04 晚改道：WorldMap 停服
后不降纯人工——机构聚合 API 实测有活的）。

三通道（2026-11-04 cheap-1 经 mihomo 池 100.64.0.10:7890 探活定稿，直连被墙）：
- **Gutendex/Project Gutenberg**：``GET https://gutendex.com/books/?search=<q>``
  → 公版书 7 万+本；``copyright=false`` → cc0_pd 可商用；True 跳过；None（美国口径
  不明）→ 宁限不放 metadata_only。
- **Wikibooks**：``GET https://en.wikibooks.org/w/api.php?action=query&list=search
  &srsearch=<q>`` → CC BY-SA 逐页 → oa_cc_by。
- **DOAB**（开放获取学术图书）：OAI-PMH ``ListRecords&metadataPrefix=oai_dc``（带
  resumptionToken 分页；REST /rest 路径拒机房 IP 不用）；dc:rights 逐书许可归层，
  未知 → metadata_only。

出海代理：环境变量 ``EDU_OER_PROXY``（如 http://100.64.0.10:7890）在场时经其出海；
不在场直连（海外部署形态）。三源均零凭据（OER Commons token 制另挂申请）。

纪律（spec cap1 新 R「OER 聚合器经 partner 通道接入」）：
- 只编目元数据+外链（body_state=link_only），正文不取——Gutenberg 公版正文虽合法，
  正文道枚举（rss_enclosure/oa_pdf/cc_course_md）尚无公版书通道，宁不伪装已落；
- 逐条四字段溯源（source_url/source/scraped_at/license），过发布闸后进查询面；
- 拒接层闸照用（gate.assert_catalog_source_allowed，影子库不收）。
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from spiders.education_card_catalog.gate import assert_catalog_source_allowed
from spiders.education_cc_courses.spider import classify_license

SOURCE = "education-oer-aggregators"
TIMEOUT = 60

GUTENDEX_API = "https://gutendex.com/books/"
WIKIBOOKS_API = "https://en.wikibooks.org/w/api.php"
DOAB_OAI = "https://directory.doabooks.org/oai/request"  # /oai 是 HTML 壳；OAI-PMH 端点在 /oai/request（2026-11-04 实证）


def _fetch(url: str) -> bytes:
    """单次 GET（EDU_OER_PROXY 在场经代理出海；异常上抛由 run* 归一）。"""
    handlers = []
    proxy = os.environ.get("EDU_OER_PROXY", "").strip()
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": "fd-industry-data/1.0 (education-oer-aggregators)"})
    last_err: Exception | None = None
    for attempt in range(2):  # 代理链路偶发 IncompleteRead/超时——重试一次
        try:
            with opener.open(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001 - 归一后上抛
            last_err = e
            time.sleep(2)
    raise last_err


def _row(resource_id: str, title: str, *, platform: str, education_source: str,
         source_url: str, license: str | None, creators: list[str] | None = None,
         notes: str | None = None, extra: dict | None = None) -> dict:
    """聚合器统一行：四字段溯源 + 许可归层（宁限不放）+ partner 标记 + link_only。

    许可语义覆盖：``license=None``（聚合器未声明书级许可）时书目本身按未知处理
    ——元数据可编目（层=metadata_only），但 commercial_ok 强制 False（宁限不放；
    classify_license 的 None 默认 True 是「元数据非著作权物」口径，不适用书级）。
    """
    layer, commercial_ok = classify_license(license)
    if not (license or "").strip():
        commercial_ok = False
    row = {
        "resource_id": resource_id,
        "title": title,
        "platform": platform,
        "creators": creators,
        "license": license or "metadata-only",
        "license_layer": layer,
        "commercial_ok": commercial_ok,
        "body_state": "link_only",
        "acquisition": "partner",
        "education_source": education_source,
        "source": SOURCE,
        "source_url": source_url,
        "notes": notes,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        row.update(extra)
    assert_catalog_source_allowed(source_url)  # 拒接层闸（影子库）照用
    return row


# ──────────────────────────────────────────────────────────────────────────────
# Gutendex / Project Gutenberg（公版书）
# ──────────────────────────────────────────────────────────────────────────────

def run_gutendex_catalog(query: str = "education", limit: int = 20) -> list[dict]:
    """Gutendex 检索 → 公版书编目行（copyright=false→cc0_pd；True 跳过；None 降级）。"""
    page = json.loads(_fetch(f"{GUTENDEX_API}?{urllib.parse.urlencode({'search': query, 'page': 1})}"))
    rows = []
    for b in page.get("results") or []:
        if len(rows) >= limit:
            break
        gid = b.get("id")
        title = (b.get("title") or "").strip()
        if not gid or not title:
            continue
        copyright_flag = b.get("copyright")  # false=公版；True=Gutenberg 修正版权版；None=美国口径不明
        if copyright_flag is True:
            continue
        authors = [a.get("name") for a in (b.get("authors") or []) if a.get("name")]
        formats = b.get("formats") or {}
        landing = formats.get("text/html") or f"https://www.gutenberg.org/ebooks/{gid}"
        license = "public domain" if copyright_flag is False else None
        rows.append(_row(
            f"edu.book.gutenberg.{gid}", title,
            platform="Project Gutenberg", education_source="gutenberg_catalog",
            source_url=landing, license=license, creators=authors,
            notes=f"subjects={json.dumps(b.get('subjects') or [], ensure_ascii=False)[:200]}",
            extra={"gutenberg_id": gid, "download_count": b.get("download_count")},
        ))
    if not rows:
        raise RuntimeError(f"Gutendex returned no usable rows for query={query!r} — upstream changed?")
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# Wikibooks（CC BY-SA 教材/指南）
# ──────────────────────────────────────────────────────────────────────────────

def run_wikibooks_catalog(query: str = "open textbook", limit: int = 20, lang: str = "en") -> list[dict]:
    """Wikibooks 全文检索 → CC BY-SA 页编目行。"""
    params = {
        "action": "query", "list": "search", "srsearch": query,
        "srlimit": str(min(max(limit, 1), 50)), "format": "json",
        "srprop": "timestamp",
    }
    host = f"https://{lang}.wikibooks.org/w/api.php"
    page = json.loads(_fetch(f"{host}?{urllib.parse.urlencode(params)}"))
    hits = ((page.get("query") or {}).get("search")) or []
    rows = []
    for h in hits:
        if len(rows) >= limit:
            break
        title = (h.get("title") or "").strip()
        if not title:
            continue
        rows.append(_row(
            f"edu.book.wikibooks.{lang}.{h.get('pageid')}", title,
            platform=f"Wikibooks ({lang})", education_source="wikibooks_search",
            source_url=f"https://{lang}.wikibooks.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
            license="CC BY-SA",  # Wikibooks 站点级许可
            notes=f"wordcount={h.get('wordcount')} timestamp={h.get('timestamp')}",
            extra={"wikibooks_pageid": h.get("pageid")},
        ))
    if not rows:
        raise RuntimeError(f"Wikibooks returned no rows for query={query!r} — upstream changed?")
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# DOAB（OAI-PMH harvest）
# ──────────────────────────────────────────────────────────────────────────────

_CC_RE = re.compile(r"creativecommons\.org/licenses/([a-z\-]+)", re.I)
_HANDLE_RE = re.compile(r"handle/([\w./-]+)")


def _license_from_dc(rights_values: list[str]) -> str | None:
    """dc:rights → 许可串（creativecommons URL 提取，短码补 cc- 前缀归一；未知 None=宁限不放）。"""
    for v in rights_values:
        m = _CC_RE.search(v or "")
        if m:
            code = m.group(1)
            return code if code.startswith("cc-") else f"cc-{code}"
    return None


def _parse_oai_record(rec: ET.Element) -> dict | None:
    """单条 oai_dc record → dc 字段 dict（命名空间无关）。"""
    meta = rec.find(".//{http://www.openarchives.org/OAI/2.0/}metadata")
    if meta is None or len(meta) == 0:
        return None
    q = rec.find(".//{http://www.openarchives.org/OAI/2.0/}header/{http://www.openarchives.org/OAI/2.0/}identifier")
    identifier = (q.text or "").strip() if q is not None else ""

    def dc_all(tag: str) -> list[str]:
        return [(e.text or "").strip() for e in meta.iter()
                if e.tag.endswith(f"}}" + tag) and (e.text or "").strip()]

    titles = dc_all("title")
    if not titles:
        return None
    idents = dc_all("identifier")
    handle_url = next((u for u in idents if "/handle/" in u), "")
    m = _HANDLE_RE.search(handle_url)
    handle = m.group(1).rstrip(".") if m else (identifier.split(":")[-1] if identifier else None)
    return {
        "identifier": identifier,
        "handle": handle,
        "title": titles[0],
        "creators": dc_all("creator"),
        "rights": dc_all("rights"),
        "handle_url": handle_url or (f"https://directory.doabooks.org/handle/{handle}" if handle else None),
    }


def run_doab_oai_harvest(limit: int = 20, metadata_prefix: str = "oai_dc") -> list[dict]:
    """DOAB OAI-PMH ListRecords harvest（resumptionToken 分页，limit 即停）。"""
    rows: list[dict] = []
    url = f"{DOAB_OAI}?verb=ListRecords&metadataPrefix={urllib.parse.quote(metadata_prefix)}"
    while len(rows) < limit and url:
        xml_bytes = _fetch(url)
        root = ET.fromstring(xml_bytes)
        err = root.find(".//{http://www.openarchives.org/OAI/2.0/}error")
        if err is not None and len(rows) == 0:
            raise RuntimeError(f"DOAB OAI error: {err.get('code')} {(err.text or '').strip()[:120]}")
        for rec in root.iter("{http://www.openarchives.org/OAI/2.0/}record"):
            if len(rows) >= limit:
                break
            d = _parse_oai_record(rec)
            if not d or not d["handle_url"]:
                continue
            license = _license_from_dc(d["rights"])
            rows.append(_row(
                f"edu.book.doab.{d['handle']}", d["title"],
                platform="DOAB", education_source="doab_oai",
                source_url=d["handle_url"], license=license,
                creators=d["creators"] or None,
                notes=f"oai_id={d['identifier']}; rights={json.dumps(d['rights'], ensure_ascii=False)[:150]}",
                extra={"doab_handle": d["handle"]},
            ))
        token_el = root.find(".//{http://www.openarchives.org/OAI/2.0/}resumptionToken")
        token = (token_el.text or "").strip() if token_el is not None else ""
        url = f"{DOAB_OAI}?verb=ListRecords&resumptionToken={urllib.parse.quote(token)}" if token else None
    if not rows:
        raise RuntimeError("DOAB OAI-PMH returned no records — upstream changed or blocked")
    return rows


def run_education_oer_aggregators(limit: int = 20) -> list[dict]:
    """runner_cli 约定入口（slug 连字符→下划线）：本源主入口 = Gutendex/Gutenberg 编目。

    本单元三具名命令（run_gutendex_catalog / run_wikibooks_catalog /
    run_doab_oai_harvest）均由 provider.py 按名分派，但巡检/调度只解析
    ``run_<slug>`` 一个入口；缺此包装时多 run_* → exit 4 且报到前失败（盲区）。
    主入口取 Gutendex（零凭据、声明面首个命令），透传 ``limit``。
    """
    return run_gutendex_catalog(limit=limit)


if __name__ == "__main__":
    for r in run_gutendex_catalog(limit=3):
        print("Gutenberg", r["license_layer"], "|", r["title"][:50])
    for r in run_wikibooks_catalog(limit=3):
        print("Wikibooks", r["license_layer"], "|", r["title"][:50])
