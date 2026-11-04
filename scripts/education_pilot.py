"""教育垂域试点批次（2026-11-04，本地小体量真数据——8.2 判据①②的预演件）。

跑各教育蜘蛛限量批次 → 合成试点目录 docs/education/pilot/catalog.jsonl →
查询面（edu_catalog.tools）出概况 → 抽样 20 条回访 source_url 可对照性。
只产本地文件与报告，不触任何生产面；集群全量批待放行后把各源 limit 放大即可。

用法：cd fd-industry-data && python scripts/education_pilot.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT.parent / "docs" / "education" / "pilot"
OUT.mkdir(parents=True, exist_ok=True)
CAT = OUT / "catalog.jsonl"
REPORT = OUT / "REPORT.md"

rows: list[dict] = []
log: list[str] = []


def _add(tag: str, new_rows: list[dict]):
    for r in new_rows:
        r["resource_type_hint"] = tag
    rows.extend(new_rows)
    log.append(f"{tag}: +{len(new_rows)} rows")
    print(f"  {tag}: +{len(new_rows)}")


def main():
    # 1) OpenAlex 教育子域骨架（1 页）
    from spiders.education_openalex.spider import run_education_openalex
    _add("openalex_skeleton", run_education_openalex(limit=150, phase="skeleton"))

    # 2) EPMC 教育 OA（元数据层）+ arXiv 新列表
    from spiders.education_oa_fulltext.spider import run_education_arxiv_new, run_education_epmc_oa
    _add("epmc_oa", run_education_epmc_oa(limit=30))
    _add("arxiv_new", run_education_arxiv_new(limit=10))

    # 3) OCW sitemap 全量编目（cap 300）+ Open edX 目录（2 页）
    from spiders.education_cc_courses.spider import run_ocw_catalog, run_openedx_catalog
    _add("ocw_catalog", run_ocw_catalog(limit=300))
    _add("openedx_catalog", run_openedx_catalog(limit=60))

    # 4) 播客 seed（小规模）
    from spiders.education_podcast.spider import run_podcast_seed
    _add("podcast_seed", run_podcast_seed(keywords=["science history", "language learning",
                                                    "财经", "历史"], countries=["us", "cn"], limit=6))

    # 5) 卡层人工卡（URL 当场可达者才收）
    from spiders.education_card_catalog.spider import run_card_curated_import
    candidates = [
        {"title": "Machine Learning Specialization", "platform": "Coursera",
         "org": "DeepLearning.AI / Stanford Online",
         "source_url": "https://www.coursera.org/specializations/machine-learning-introduction",
         "acquisition": "curated"},
        {"title": "Introduction to Algorithms", "platform": "OCW 卡片(外链示范)",
         "source_url": "https://ocw.mit.edu/courses/6-006-introduction-to-algorithms-spring-2020/",
         "acquisition": "curated"},
        {"title": "中国大学MOOC·机器学习", "platform": "icourse163",
         "source_url": "https://www.icourse163.org/course/PKU-100199552",
         "acquisition": "curated"},
    ]
    reachable, skipped = [], []
    for c in candidates:
        try:
            req = urllib.request.Request(c["source_url"], method="HEAD",
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                reachable.append(c) if resp.status < 400 else skipped.append((c["title"], resp.status))
        except Exception as e:
            skipped.append((c["title"], f"unreachable: {type(e).__name__}"))
        time.sleep(0.5)
    res = run_card_curated_import(reachable)
    _add("card_curated", res["rows"])
    for t, why in skipped:
        log.append(f"card skipped: {t} ({why})")

    # ── 落盘试点目录 ──
    CAT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    from edu_catalog.store import CatalogStore, SubscriptionStore
    from edu_catalog import tools
    funcs = {}
    class R:
        def tool(self, fn): funcs[fn.__name__] = fn
    tools.register(R(), catalog=lambda: CatalogStore(CAT),
                   subs=SubscriptionStore(OUT / "subscriptions.json"))
    ov = funcs["edu_overview"]()

    # ── 判据② 抽样回访：跨源取 20 条发布行，GET 首页核对题录 ──
    sample = []
    seen_type: dict[str, int] = {}
    for r in rows:
        h = r["resource_type_hint"]
        if seen_type.get(h, 0) >= 4:
            continue
        ok, _ = tools.publish_gate(r)
        if not ok:
            continue
        seen_type[h] = seen_type.get(h, 0) + 1
        sample.append(r)
        if len(sample) >= 20:
            break
    revisit = []
    for r in sample:
        url = r.get("source_url") or ""
        title = (r.get("title") or "")[:60]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                body = resp.read(200000).decode("utf-8", "replace").lower()
                # 可对照性粗核：题录首词出现在页面（或 200 页面本身）
                probe = title.split(" ")[0].lower() if title else ""
                match = probe in body if probe else None
                revisit.append((url[:70], resp.status, "ok" if resp.status < 400 else "http_error",
                                f"title-match={match}"))
        except Exception as e:
            reason = "unreachable"
            s = str(e).lower()
            if "403" in s or "challenge" in s: reason = "blocked(403/challenge)"
            elif "timed out" in s: reason = "timeout"
            revisit.append((url[:70], "-", reason, str(e)[:40]))
        time.sleep(0.4)

    # ── 报告 ──
    lines = ["# 教育垂域试点批次 REPORT（2026-11-04）", "",
             "本地小体量真数据预演（8.2 判据①②预跑）；集群全量批待放行后放大 limit 即复用本管线。", "",
             "## 批次", *log, "",
             f"目录行合计: {len(rows)}", "",
             "## 判据① 许可层计数（查询面概况自证）",
             "```json", json.dumps(ov, ensure_ascii=False, indent=1)[:1500], "```", "",
             f"## 判据② 抽样回访 {len(revisit)} 条（source_url 可达+题录粗对照）", ""]
    for u, st, outcome, note in revisit:
        lines.append(f"- {u} | status={st} | {outcome} | {note}")
    lines += ["", "## 已知边界", "",
              "- blocked/challenge 类回访失败=平台 bot 挡（如 coursera Cloudflare），卡片条目本身按 D8「人工核验」成立，回访面降级如实记录。",
              "- 试点=百行级；骨架 7.6M 行需集群季度快照作业（3.1/2.1 全量批待放行）。"]
    REPORT.write_text("\n".join(lines))
    print(f"\nREPORT -> {REPORT}")
    print(f"catalog -> {CAT} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
