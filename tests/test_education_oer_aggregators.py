"""4.2 OER 聚合器三适配器测试（Gutendex/Wikibooks/DOAB，monkeypatch _fetch 不打网）。

真 API 通路 2026-11-04 cheap-1 探针实证（gutendex 200/wikibooks 200/DOAB OAI 可达）；
本套测的是行映射、许可归层、溯源四字段与分页循环，非连通性。
"""
from __future__ import annotations

import json

import pytest

import spiders.education_oer_aggregators.spider as S
from spiders.education_card_catalog.gate import assert_catalog_source_allowed


# ── canned payloads ──────────────────────────────────────────────────────────

GUTENDEX_PAGE = {
    "count": 100, "next": None, "previous": None,
    "results": [
        {"id": 52821, "title": "Kant's Prolegomena", "copyright": False,
         "authors": [{"name": "Kant, Immanuel", "birth_year": 1724}],
         "subjects": ["Ethics"], "formats": {"text/html": "https://www.gutenberg.org/ebooks/52821.html"},
         "download_count": 99},
        {"id": 11111, "title": "Copyrighted Variant", "copyright": True, "authors": []},
        {"id": 22222, "title": "Unknown Status Book", "copyright": None, "authors": []},
    ],
}

WIKIBOOKS_PAGE = {
    "batchcomplete": "",
    "query": {"searchinfo": {"totalhits": 3093},
              "search": [
                  {"ns": 0, "title": "FHSST Physics", "pageid": 12345, "timestamp": "2026-10-01T00:00:00Z"},
                  {"ns": 0, "title": "Blender 3D", "pageid": 678, "timestamp": "2026-10-02T00:00:00Z"},
              ]},
}

_OAI_HDR = "{http://www.openarchives.org/OAI/2.0/}"


def _oai_page(records: list[dict], token: str | None) -> bytes:
    recs = ""
    for r in records:
        recs += f"""
        <record>
          <header><identifier>{r['oai']}</identifier></header>
          <metadata>
            <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                       xmlns:dc="http://purl.org/dc/elements/1.1/">
              <dc:title>{r['title']}</dc:title>
              <dc:creator>{r.get('creator', 'An Author')}</dc:creator>
              <dc:identifier>https://directory.doabooks.org/handle/{r['handle']}</dc:identifier>
              <dc:rights>{r.get('rights', '')}</dc:rights>
            </oai_dc:dc>
          </metadata>
        </record>"""
    tok = f"<resumptionToken>{token}</resumptionToken>" if token else "<resumptionToken/>"
    return (f"""<?xml version="1.0"?>
    <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><responseDate>2026-11-04</responseDate>
    <request verb="ListRecords"/>
    <ListRecords>{recs}{tok}</ListRecords></OAI-PMH>""").encode()


def test_gutendex_license_layers_and_skip():
    """公版→cc0_pd/可商用；copyright=true 跳过；未知→宁限不放 metadata_only。"""
    S._fetch = lambda url: json.dumps(GUTENDEX_PAGE).encode()
    rows = S.run_gutendex_catalog(limit=10)
    by_id = {r["resource_id"]: r for r in rows}
    assert len(rows) == 2  # copyright=true 被跳过
    pub = by_id["edu.book.gutenberg.52821"]
    assert pub["license_layer"] == "cc0_pd" and pub["commercial_ok"] is True
    assert pub["creators"] == ["Kant, Immanuel"]
    assert pub["source_url"] == "https://www.gutenberg.org/ebooks/52821.html"
    assert pub["acquisition"] == "partner" and pub["body_state"] == "link_only"
    unk = by_id["edu.book.gutenberg.22222"]
    assert unk["license_layer"] == "metadata_only" and unk["commercial_ok"] is False


def test_gutendex_traceability_four_fields():
    """溯源四字段全行必带（cap1 R4）+ 过拒接层闸。"""
    S._fetch = lambda url: json.dumps(GUTENDEX_PAGE).encode()
    for r in S.run_gutendex_catalog():
        assert r["source_url"] and r["source"] and r["scraped_at"] and r["license"]
        assert_catalog_source_allowed(r["source_url"])


def test_wikibooks_cc_by_sa_layer():
    """CC BY-SA → oa_cc_by 可商用；source_url 为 wiki 页。"""
    S._fetch = lambda url: json.dumps(WIKIBOOKS_PAGE).encode()
    rows = S.run_wikibooks_catalog(query="physics")
    assert len(rows) == 2
    r0 = rows[0]
    assert r0["license_layer"] == "oa_cc_by" and r0["commercial_ok"] is True
    assert r0["license"] == "CC BY-SA"
    assert r0["resource_id"] == "edu.book.wikibooks.en.12345"
    assert r0["source_url"] == "https://en.wikibooks.org/wiki/FHSST_Physics"
    assert r0["education_source"] == "wikibooks_search"


def test_doab_oai_resumption_and_license_mapping():
    """OAI 两页 resumptionToken 走通；rights CC URL → 许可层；未知降 metadata_only。"""
    page1 = _oai_page([
        {"oai": "oai:doab:1", "handle": "20.500.12854/1", "title": "CC BY Book",
         "rights": "https://creativecommons.org/licenses/by/4.0/"},
        {"oai": "oai:doab:2", "handle": "20.500.12854/2", "title": "CC BY-NC Book",
         "rights": "https://creativecommons.org/licenses/by-nc/4.0/"},
    ], token="tok1")
    page2 = _oai_page([
        {"oai": "oai:doab:3", "handle": "20.500.12854/3", "title": "No Rights Book", "rights": ""},
    ], token=None)
    seen_urls = []

    def fake_fetch(url):
        seen_urls.append(url)
        return page1 if len(seen_urls) == 1 else page2

    S._fetch = fake_fetch
    rows = S.run_doab_oai_harvest(limit=10)
    assert len(rows) == 3
    assert "resumptionToken=tok1" in seen_urls[1]  # 分页接上了
    by_handle = {r["resource_id"]: r for r in rows}
    assert by_handle["edu.book.doab.20.500.12854/1"]["license_layer"] == "oa_cc_by"
    assert by_handle["edu.book.doab.20.500.12854/1"]["commercial_ok"] is True
    assert by_handle["edu.book.doab.20.500.12854/2"]["license_layer"] == "cc-by-nc"
    assert by_handle["edu.book.doab.20.500.12854/2"]["commercial_ok"] is False
    unk = by_handle["edu.book.doab.20.500.12854/3"]
    assert unk["license_layer"] == "metadata_only" and unk["commercial_ok"] is False
    assert unk["source_url"].startswith("https://directory.doabooks.org/handle/")


def test_doab_limit_stops_early():
    """limit=1 只取一页第一条（resumptionToken 仍存在但循环停）。"""
    page = _oai_page([
        {"oai": "oai:doab:1", "handle": "h1", "title": "A", "rights": ""},
        {"oai": "oai:doab:2", "handle": "h2", "title": "B", "rights": ""},
    ], token="tokX")
    S._fetch = lambda url: page
    rows = S.run_doab_oai_harvest(limit=1)
    assert len(rows) == 1


def test_publish_gate_passes_on_all_aggregator_rows():
    """三源行全部过发布闸（source_url/source/scraped_at/license 四字段）。"""
    from spiders.education_catalog_core import publish_gate
    def ok(url):
        return url.encode()
    S._fetch = lambda url: json.dumps(GUTENDEX_PAGE).encode()
    gut = S.run_gutendex_catalog()
    S._fetch = lambda url: json.dumps(WIKIBOOKS_PAGE).encode()
    wiki = S.run_wikibooks_catalog()
    for r in gut + wiki:
        passed, missing = publish_gate(r)
        assert passed, (r["resource_id"], missing)


def test_empty_results_raise_loudly():
    """上游返回空 = 显式报错（不产空目录、不伪装无匹配）。"""
    S._fetch = lambda url: json.dumps({"results": []}).encode()
    with pytest.raises(RuntimeError, match="no usable rows"):
        S.run_gutendex_catalog()
