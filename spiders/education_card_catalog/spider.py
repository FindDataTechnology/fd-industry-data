"""spiders/education_card_catalog —— 卡层：curated/partner 人工录入 + 卡片 schema 纪律。

卡层纪律（spec cap1 R2 仅卡片层 + R7 负面清单；design D8 禁爬虫规模化）：
- 录入通道只有两条：``acquisition ∈ {curated, partner}``——本蜘蛛不发起任何网络请求；
- 卡片必带：title/platform/source_url（原站外链）/license_status/「本目录未取正文」
  明示字段 ``fulltext_acquired=False``（字段级强制，缺/反即拒行）；
- 带正文内容（body_markdown/body_text 字段）的卡片一律拒收——卡层没有正文道；
- 拒接层域名（影子图书馆）连元数据都拒收（gate.assert_catalog_source_allowed）。

5.1 验证走真课：Coursera《Machine Learning Specialization》人工卡样例（tests）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from .gate import assert_catalog_source_allowed

SOURCE = "education-card-catalog"

BODY_FIELDS = ("body_markdown", "body_text", "transcript", "fulltext")


def run_card_curated_import(cards: list[dict]) -> dict:
    """卡层人工/合作录入：校验+归一，返回 {"rows": [...], "rejected": [...]}。

    纯录入函数——零网络。每行输出四字段溯源（source_url=原站外链、
    source=本通道、scraped_at=录入时间、license=platform-content）+ 禁正文标记。
    """
    if not isinstance(cards, list):
        raise TypeError("cards must be a list of dicts")
    now = datetime.now(timezone.utc).isoformat()
    rows, rejected = [], []
    for i, c in enumerate(cards):
        if not isinstance(c, dict):
            rejected.append({"index": i, "reason": "not a dict"})
            continue
        title = (c.get("title") or "").strip()
        platform = (c.get("platform") or "").strip()
        source_url = (c.get("source_url") or "").strip()
        acquisition = c.get("acquisition")
        problems = []
        if not title:
            problems.append("missing title")
        if not platform:
            problems.append("missing platform")
        if not source_url:
            problems.append("missing source_url (原站外链必填)")
        if acquisition not in ("curated", "partner"):
            problems.append(f"acquisition={acquisition!r} must be curated|partner (D8: no crawler channel)")
        for f in BODY_FIELDS:
            if c.get(f):
                problems.append(f"card layer must not carry body field {f!r} (spec cap1 R2/R7)")
        try:
            assert_catalog_source_allowed(source_url)
        except ValueError as e:
            problems.append(str(e))
        if problems:
            rejected.append({"index": i, "title": title or None, "reason": "; ".join(problems)})
            continue
        rows.append({
            "resource_id": c.get("resource_id") or f"edu.card.{platform.lower().replace(' ', '-')}.{i}",
            "title": title,
            "platform": platform,
            "org": c.get("org"),
            "creators": c.get("creators"),
            "license_status": "platform-content",
            "license": "metadata-only",
            "license_layer": "metadata_only",
            "commercial_ok": False,  # 商业平台内容授权不明——商用默认拒
            "body_state": "link_only",
            "fulltext_acquired": False,  # 明示：本目录未取正文
            "acquisition": acquisition,
            "education_source": "card_curated",
            "source": SOURCE,
            "source_url": source_url,
            "notes": c.get("notes"),
            "scraped_at": now,
        })
    return {"rows": rows, "rejected": rejected}


if __name__ == "__main__":
    demo = [{"title": "Machine Learning Specialization", "platform": "Coursera",
             "org": "DeepLearning.AI / Stanford Online",
             "source_url": "https://www.coursera.org/specializations/machine-learning-introduction",
             "acquisition": "curated"}]
    print(run_card_curated_import(demo))
