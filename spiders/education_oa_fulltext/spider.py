"""spiders/education_oa_fulltext —— 教育域 OA 正文层（arXiv 新列表 + Europe PMC OA 过滤 → markdown 提取）。

数据表面（Wave 0 1.2 许可复核通过的两源）：

- arXiv API（元数据 CC0 1.0）：``GET http://export.arxiv.org/api/query?search_query=cat:cs.CY+OR+cat:physics.ed-ph&sortBy=submittedDate&sortOrder=descending&max_results=N``
  Atom XML，新提交列表天然增量。教育相关类目：cs.CY（计算机与社会/教育技术）、
  physics.ed-ph（物理教育）。PDF 版权归作者——正文提取层只做内部索引用途（D6），
  再分发被用途闸拦截。
- Europe PMC REST（逐篇 CC/PD）：``GET https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=<教育检索词>+AND+OPEN_ACCESS:Y&format=json``
  → 命中条目带 fullTextIdList（PMC ID）→ ``GET .../PMC{id}/fullTextXML`` JATS XML → markdown。

产出行（D6 兼容 OpenMAIC materials 通道）：
- ``body_markdown``：提取正文（PDF pypdf / JATS XML 转换）
- ``body_source``：arxiv_pdf | epmc_jats
- ``license``：逐篇（arXiv 元数据 cc0；EPMC 取 license 字段，缺省 oa-unspecified）
- sidecar 元数据即行内字段（title/creators/doi/as_of/source_ref），落库层据此生成 sidecar。

口坑：
- arXiv API 是 http（非 https），urllib 需显式允许；限速 3s/请求（官方礼貌要求）。
- EPMC cursorMark 深翻页；本蜘蛛默认一页 25 条（pageSize=25）。
- pypdf 提取失败（扫描件/加密）→ body_markdown=None，body_state 回 link_only，不伪装成功。
"""
from __future__ import annotations

import gzip
import io
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

SOURCE = "education-oa-fulltext"
ARXIV_API = "http://export.arxiv.org/api/query"
EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EPMC_FULLTEXT = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"

ARXIV_EDU_CATEGORIES = ["cat:cs.CY", "cat:physics.ed-ph"]  # 教育技术 / 物理教育
EPMC_EDU_QUERY = '(TITLE:"education" OR TITLE:"teaching" OR TITLE:"learning")'

ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
JATS_TAGS_STRIP = re.compile(r"</?(?!(?:p|sec|title|abstract|body|ref-list)\b)[a-zA-Z0-9:._-]+[^>]*>")


def _fetch(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "fd-industry-data/1.0 (education-oa-fulltext)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ── arXiv ────────────────────────────────────────────────────────────────────

def _arxiv_to_markdown(pdf_bytes: bytes) -> str | None:
    """pypdf 提取 PDF 文本（失败返回 None，不伪装）。"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        text = "\n\n".join(p for p in pages if p.strip())
        return text if text.strip() else None
    except Exception:
        return None


def _parse_arxiv_atom(atom_xml: bytes) -> list[dict]:
    """arXiv Atom → 条目元数据列表。"""
    root = ET.fromstring(atom_xml)
    out = []
    for entry in root.findall("a:entry", ATOM_NS):
        arxiv_id = (entry.findtext("a:id", "", ATOM_NS) or "").rsplit("/", 1)[-1]
        title = (entry.findtext("a:title", "", ATOM_NS) or "").strip().replace("\n", " ")
        summary = (entry.findtext("a:summary", "", ATOM_NS) or "").strip()
        published = entry.findtext("a:published", "", ATOM_NS)
        authors = [a.findtext("a:name", "", ATOM_NS) for a in entry.findall("a:author", ATOM_NS)]
        doi = None
        for link in entry.findall("a:link", ATOM_NS):
            if link.get("title") == "doi":
                doi = link.get("href")
        pdf_url = next((l.get("href") for l in entry.findall("a:link", ATOM_NS)
                        if (l.get("title") or "").lower() == "pdf"), None)
        cats = [c.get("term") for c in entry.findall("a:category", ATOM_NS)]
        out.append({
            "arxiv_id": arxiv_id,
            "title": title,
            "abstract": summary,
            "published": published,
            "authors": authors,
            "doi": doi,
            "pdf_url": pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
            "categories": [c for c in cats if c],
        })
    return out


def run_education_arxiv_new(limit: int = 10, fetch_bodies: bool = False) -> list[dict]:
    """arXiv 教育类目新提交列表（天然增量：按 submittedDate 倒序）。"""
    # 空格连接（urlencode 会把空格编成 +，arXiv 按 OR 解析）；切勿手写 '+OR+'——
    # urlencode 会把 + 编成 %2B（字面加号），查询变零命中（2026-11-04 实测踩坑）。
    query = " OR ".join(ARXIV_EDU_CATEGORIES)
    params = urllib.parse.urlencode({
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": min(limit, 100),
    })
    atom = _fetch(f"{ARXIV_API}?{params}")
    time.sleep(3)  # arXiv 礼貌间隔
    entries = _parse_arxiv_atom(atom)
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for e in entries:
        row = {
            "resource_id": f"edu.paper.arxiv.{e['arxiv_id']}",
            "work_id": f"arxiv:{e['arxiv_id']}",
            "doi": e["doi"],
            "title": e["title"],
            "display_name": e["title"],
            "abstract": e["abstract"],
            "publication_date": (e["published"] or "")[:10] or None,
            "authors": json.dumps(e["authors"], ensure_ascii=False),
            "license": "cc0",  # arXiv 元数据 CC0 1.0（正文版权归作者，内部索引）
            "body_state": "link_only",
            "body_source": None,
            "body_markdown": None,
            "pdf_url": e["pdf_url"],
            "education_source": "arxiv_new",
            "source": SOURCE,
            "source_url": f"https://arxiv.org/abs/{e['arxiv_id']}",
            "scraped_at": now,
        }
        if fetch_bodies and e["pdf_url"]:
            try:
                md = _arxiv_to_markdown(_fetch(e["pdf_url"], timeout=120))
                if md:
                    row["body_markdown"] = md
                    row["body_state"] = "stored_extraction"
                    row["body_source"] = "arxiv_pdf"
            except Exception:
                pass  # 显式保持 link_only，不伪装成功
        rows.append(row)
    return rows


# ── Europe PMC ───────────────────────────────────────────────────────────────

def _jats_to_markdown(xml_bytes: bytes) -> str | None:
    """JATS XML → markdown（粗转换：section 标题与段落保序）。"""
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return None
    body = root.find(".//body")
    if body is None:
        return None
    parts: list[str] = []
    for sec in body.iter("sec"):
        title = sec.findtext("title")
        if title:
            parts.append(f"## {title.strip()}")
        for p in sec.findall("p"):
            text = "".join(p.itertext()).strip()
            if text:
                parts.append(text)
    if not parts:  # 无 sec 结构，退化为全部段落
        for p in body.findall("p"):
            text = "".join(p.itertext()).strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts) if parts else None


def run_education_epmc_oa(limit: int = 10, fetch_bodies: bool = False) -> list[dict]:
    """Europe PMC 教育 OA 检索（元数据 CC0；全文 XML 仅 OA 标记者）。"""
    params = urllib.parse.urlencode({
        "query": f"{EPMC_EDU_QUERY} AND OPEN_ACCESS:Y",
        "format": "json",
        "pageSize": min(limit, 100),
        "resultType": "core",
    })
    payload = json.loads(_fetch(f"{EPMC_SEARCH}?{params}"))
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for hit in (payload.get("resultList", {}) or {}).get("result", []):
        pmcid = hit.get("pmcid")
        license = hit.get("license") or "oa-unspecified"
        row = {
            "resource_id": f"edu.paper.pmc.{pmcid or hit.get('id')}",
            "work_id": f"pmc:{pmcid}" if pmcid else f"pmid:{hit.get('pmid')}",
            "doi": hit.get("doi"),
            "title": hit.get("title"),
            "display_name": hit.get("title"),
            "abstract": hit.get("abstractText"),
            "publication_date": hit.get("firstPublicationDate") or hit.get("firstIndexDate"),
            "authors": json.dumps([a.get("fullName") for a in (hit.get("authorList") or {}).get("author", [])], ensure_ascii=False),
            "journal": hit.get("journalTitle"),
            "license": license,
            "body_state": "link_only",
            "body_source": None,
            "body_markdown": None,
            "source": SOURCE,
            "source_url": (f"https://europepmc.org/article/{pmcid}" if pmcid
                           else f"https://europepmc.org/article/MED/{hit.get('pmid')}"),
            "education_source": "epmc_oa",
            "scraped_at": now,
        }
        if fetch_bodies and pmcid and hit.get("isOpenAccess") == "Y":
            try:
                md = _jats_to_markdown(_fetch(EPMC_FULLTEXT.format(pmcid=pmcid), timeout=60))
                if md:
                    row["body_markdown"] = md
                    row["body_state"] = "stored_extraction"
                    row["body_source"] = "epmc_jats"
            except Exception:
                pass
        rows.append(row)
    return rows


# ── 兼容 OpenAlex OA 层的统一入口（provider 挂载用）─────────────────────────

def run_education_oa_fulltext(limit: int = 10, source: str = "epmc", fetch_bodies: bool = False) -> list[dict]:
    """OA 正文层统一入口。source: 'epmc' | 'arxiv'。"""
    if source == "arxiv":
        return run_education_arxiv_new(limit=limit, fetch_bodies=fetch_bodies)
    return run_education_epmc_oa(limit=limit, fetch_bodies=fetch_bodies)


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"Test epmc limit={n} (metadata only)")
    rows = run_education_epmc_oa(limit=n)
    for r in rows[:2]:
        print(f"  {r['title'][:60]} | license={r['license']} | oa={r['body_state']}")
    print(f"Test arxiv limit={n} (metadata only)")
    rows = run_education_arxiv_new(limit=n)
    for r in rows[:2]:
        print(f"  {r['title'][:60]} | license={r['license']}")
