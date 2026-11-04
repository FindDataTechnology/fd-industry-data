"""Wave 3 CC 课程层测试（spec cap1 R2 许可分层 + 用途闸判定函数）。

- 4.3 classify_license 纯函数四枚举覆盖（cc0_pd / oa_cc_by / cc-by-nc / metadata_only）
- NC→commercial_ok=False 逐档断言；未知一律降 metadata_only（宁限不放）
- 在线冒烟：OCW sitemap 真行四字段齐备+禁商用标记；Open edX name 字段映射
"""
from __future__ import annotations

import pytest


# ── 4.3 用途闸判定（离线纯函数）────────────────────────────────────────────

@pytest.mark.parametrize("lic, layer, ok", [
    ("CC0", "cc0_pd", True),
    ("public-domain", "cc0_pd", True),
    ("cc-by", "oa_cc_by", True),
    ("CC BY 4.0", "oa_cc_by", True),
    ("CC BY-SA 4.0", "oa_cc_by", True),
    ("CC BY-NC-SA", "cc-by-nc", False),
    ("cc-by-nc", "cc-by-nc", False),
    ("CC BY-NC 4.0", "cc-by-nc", False),
    (None, "metadata_only", True),
    ("", "metadata_only", True),
    ("weird-license", "metadata_only", True),  # 未识别降级，不冒全文
])
def test_classify_license_layers(lic, layer, ok):
    from spiders.education_cc_courses.spider import classify_license
    assert classify_license(lic) == (layer, ok)


# ── 在线冒烟 ─────────────────────────────────────────────────────────────────

@pytest.mark.online
def test_ocw_sitemap_catalog_rows_traceable():
    from spiders.education_cc_courses.spider import run_ocw_catalog
    rows = run_ocw_catalog(limit=5)
    assert rows, "OCW sitemap returned nothing"
    for r in rows:
        for field in ("source_url", "source", "scraped_at", "license"):
            assert r.get(field), f"OCW row missing {field} (spec cap1 R4)"
        assert r["license_layer"] == "cc-by-nc"
        assert r["commercial_ok"] is False  # NC 全站禁商用
        assert r["body_state"] == "link_only"  # 正文层未落地不伪装
        assert r["source_url"].startswith("https://ocw.mit.edu/courses/")


@pytest.mark.online
def test_openedx_catalog_uses_name_field():
    from spiders.education_cc_courses.spider import run_openedx_catalog
    rows = run_openedx_catalog(limit=3)
    assert rows, "OL Library returned nothing"
    for r in rows:
        for field in ("source_url", "source", "scraped_at"):
            assert r.get(field), f"edX row missing {field}"
        assert r["title"], f"course {r['course_id']}: title (API 'name') must be present"
        assert r["body_state"] == "link_only"  # 视频外链，不取正文
        assert r["license_layer"] == "metadata_only"  # 实例许可未知 → 降级
