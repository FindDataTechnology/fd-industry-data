#!/usr/bin/env python3
"""四张源登记表的对照产物（change derive-source-registries）。

同一件「这个源怎么了」的事，散在四张互不对账的表里：

    manifest     spiders/*/manifest.yaml             —— 有单元、亮没亮
    extractor    scripts/{wave_extract_split,extract_*}.py —— 进不进观测层
    runner       fetch/runner.py 的 RUNNERS          —— read() 能不能按概念读
    l3           output/l3-reanalysis/registry.json  —— 最近一次可达性实测

本脚本把它们并成每源一行，四列各报各的状态。**不许压成一个「状态」**：
「有 manifest 但没抽取器」「有抽取器但不在 RUNNERS」指向不同处置。

两个维度分开
────────────
* **盘面事实**（每轮现算）：unit_present / has_manifest / lit / has_extractor / in_runners
* **可达性事实**（某次实测留下的）：verdict / evidence_at / evidence_age_days

单元目录已不存在而记录仍标 `unreachable` 的，判为 `archived`，**不计入**
`unreachable` 统计——「已删掉」与「打不通」是两件事。证据超过陈旧阈值的标 `stale`。

只读。产物落 ``reports/source-registry/<date>.{json,md}``。

用法::

    python3 scripts/source_registry.py
    python3 scripts/source_registry.py --json
    python3 scripts/source_registry.py --report
    python3 scripts/source_registry.py --self-test
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SIBLING = REPO.parent / "fd-open-data-mcp"      # adapter/runner 在兄弟仓
L3 = REPO / "output" / "l3-reanalysis" / "registry.json"
REPORTS = REPO / "reports" / "source-registry"
RUNNERS_PY = SIBLING / "fd_open_data_mcp" / "fetch" / "runner.py"
#: 证据陈旧阈值。L3 的 protocol 本意是「按需重测」而非周期重测——90 天覆盖
#: 一个季度，又不会把两个月前的证据直接判废。
STALENESS_DAYS = 90


def lit_map() -> dict[str, str]:
    import yaml

    out: dict[str, str] = {}
    for p in sorted((REPO / "spiders").glob("*/manifest.yaml")):
        try:
            m = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        name = m.get("name") or p.parent.name
        out[name] = (m.get("schedule") or "").strip()
    return out


def extractor_set() -> set[str]:
    if str(REPO / "scripts") not in sys.path:
        sys.path.insert(0, str(REPO / "scripts"))
    import check_observation_lag as col

    return col.extractor_source_names(REPO / "scripts")


def runner_set() -> set[str]:
    """RUNNERS 的键。静态读，不 import（兄弟仓可能没装依赖）。"""
    if not RUNNERS_PY.is_file():
        return set()
    text = RUNNERS_PY.read_text(encoding="utf-8")
    i = text.find("RUNNERS: dict")
    if i < 0:
        i = text.find("RUNNERS = {")
    block = text[i:text.find("}", i) + 1] if i >= 0 else ""
    return set(re.findall(r'^\s{4}"([a-z0-9\-]+)":', block, re.M))


def load_l3() -> dict:
    if not L3.is_file():
        return {"units": {}}
    return json.loads(L3.read_text(encoding="utf-8"))


def unit_exists(key: str) -> bool:
    """``spiders/x`` 在本仓，``fd_open_data_mcp/...`` 在兄弟仓。"""
    if key.startswith("spiders/"):
        return (REPO / key).is_dir()
    return (SIBLING / key).is_file()


def age_days(stamp: str | None, now: dt.date) -> int | None:
    if not stamp:
        return None
    try:
        d = dt.date.fromisoformat(stamp[:10])
    except ValueError:
        return None
    return (now - d).days


def classify_l3(record: dict | None, present: bool, now: dt.date,
                threshold: int = STALENESS_DAYS) -> tuple[str, int | None]:
    """(结论, 证据年龄)。``archived`` 优先于任何可达性结论。"""
    if record is None:
        return "unmeasured", None
    verdict = record.get("verdict") or "unmeasured"
    at = (record.get("evidence") or {}).get("at") or record.get("evidence_at")
    a = age_days(at, now)
    if not present:
        return "archived", a
    if a is not None and a > threshold:
        return "stale", a
    return verdict, a


def build(now: dt.date | None = None) -> list[dict]:
    now = now or dt.date.today()
    lit = lit_map()
    ext = extractor_set()
    run = runner_set()
    l3 = load_l3().get("units", {})
    # registry 的 key 形如 spiders/x；manifest 的源名是 x —— 两者按目录名对齐
    l3_by_name = {}
    for k, v in l3.items():
        if k.startswith("spiders/"):
            l3_by_name[k.split("/", 1)[1]] = (k, v)
        else:
            l3_by_name[k] = (k, v)

    names = set(lit) | {v["unit"].split("/")[-1] for v in l3.values()
                        if v.get("unit", "").startswith("spiders/")}
    rows: list[dict] = []
    for name in sorted(names):
        key, rec = l3_by_name.get(name, (f"spiders/{name}", None))
        present = unit_exists(key)
        verdict, age = classify_l3(rec, present, now)
        rows.append({
            "source": name,
            "manifest": name in lit,
            "lit": bool(lit.get(name)),
            "extractor": name in ext,
            "runner": name in run,
            "l3_key": key if rec else None,
            "l3_verdict": rec.get("verdict") if rec else None,
            "l3_state": verdict,
            "l3_age_days": age,
            "unit_present": present,
        })
    return rows


def summary(rows: list[dict]) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        out[r["l3_state"]] = out.get(r["l3_state"], 0) + 1
    out["_units"] = len(rows)
    out["_manifest"] = sum(1 for r in rows if r["manifest"])
    out["_lit"] = sum(1 for r in rows if r["lit"])
    out["_extractor"] = sum(1 for r in rows if r["extractor"])
    out["_runner"] = sum(1 for r in rows if r["runner"])
    return out


def drift(rows: list[dict]) -> dict:
    """双向差异：登记表指向不存在的单元 / 仓里有单元不在登记表。"""
    # 只对「l3 里有记录」的行判 archived（无记录的行不是「指向不存在」）
    reg_missing = sorted(r["source"] for r in rows
                         if r["l3_key"] and not r["unit_present"])
    in_repo_not_reg = sorted(r["source"] for r in rows
                             if r["manifest"] and not r["l3_key"])
    return {"registered_but_absent": reg_missing,
            "in_repo_not_registered": in_repo_not_reg}


def render(rows: list[dict], s: dict, dr: dict, now: dt.date) -> str:
    lines = [
        "# 四表对照（源登记表合并视图）",
        "",
        f"- 生成：{now.isoformat()}",
        f"- 合计 {s['_units']} 源：manifest {s['_manifest']} / 亮着 {s['_lit']}"
        f" / 抽取器 {s['_extractor']} / RUNNERS {s['_runner']}",
        f"- L3 结论：archived {s.get('archived', 0)} / stale {s.get('stale', 0)}"
        f" / unreachable {s.get('unreachable', 0)} / verified {s.get('verified_api', 0) + s.get('verified_html', 0)}"
        f" / degraded {s.get('degraded', 0)} / needs_browser {s.get('needs_browser', 0)}"
        f" / unmeasured {s.get('unmeasured', 0)}",
        "",
        "## 漂移",
        "",
        f"- 登记表指向但盘面不存在（{len(dr['registered_but_absent'])}）：",
    ]
    lines += [f"  - `{n}`" for n in dr["registered_but_absent"]] or ["  （无）"]
    lines += ["", f"- 仓里有单元但不在登记表（{len(dr['in_repo_not_registered'])}）："]
    lines += [f"  - `{n}`" for n in dr["in_repo_not_registered"][:30]]
    if len(dr["in_repo_not_registered"]) > 30:
        lines.append(f"  - …共 {len(dr['in_repo_not_registered'])} 个")
    lines += ["", "## 逐源", "",
              "| 源 | manifest | 亮 | 抽取器 | runner | L3 | 证据年龄 |",
              "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['source']} | {'有' if r['manifest'] else '—'} | "
            f"{'是' if r['lit'] else '否'} | {'有' if r['extractor'] else '—'} | "
            f"{'有' if r['runner'] else '—'} | {r['l3_state']} | "
            f"{r['l3_age_days'] if r['l3_age_days'] is not None else '—'} |")
    return "\n".join(lines) + "\n"


def self_test() -> int:
    now = dt.date(2026, 11, 21)
    bad = []
    # archived 优先于任何可达性结论
    got, _ = classify_l3({"verdict": "unreachable", "evidence": {"at": "2026-09-13"}},
                         present=False, now=now)
    if got != "archived":
        bad.append("已归档应判 archived 而非 unreachable")
    # 目录在但证据超阈值 → stale
    got, age = classify_l3({"verdict": "verified_html", "evidence": {"at": "2026-01-01"}},
                           present=True, now=now)
    if got != "stale" or age is None or age <= STALENESS_DAYS:
        bad.append(f"证据超阈值应判 stale，得 {got} age={age}")
    # 目录在、证据新 → 原结论
    got, _ = classify_l3({"verdict": "verified_api", "evidence": {"at": "2026-11-01"}},
                         present=True, now=now)
    if got != "verified_api":
        bad.append("新证据应保留原结论")
    # 无记录
    got, _ = classify_l3(None, present=True, now=now)
    if got != "unmeasured":
        bad.append("无记录应判 unmeasured")
    if bad:
        print("selftest FAILED:\n" + "\n".join("  " + b for b in bad), file=sys.stderr)
        return 1
    print("selftest OK（4 例：archived 优先 / stale 阈值 / 原结论 / unmeasured）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="source_registry")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()

    if not (REPO / "spiders").is_dir():
        print("判不出：spiders/ 目录不可用", file=sys.stderr)
        return 2
    now = dt.date.today()
    rows = build(now)
    s = summary(rows)
    dr = drift(rows)

    if args.report:
        REPORTS.mkdir(parents=True, exist_ok=True)
        jp, mp = REPORTS / f"{now.isoformat()}.json", REPORTS / f"{now.isoformat()}.md"
        jp.write_text(json.dumps({"generated_at": now.isoformat(),
                                  "staleness_threshold_days": STALENESS_DAYS,
                                  "summary": s, "drift": dr, "sources": rows},
                                 ensure_ascii=False, indent=2, default=str),
                      encoding="utf-8")
        mp.write_text(render(rows, s, dr, now), encoding="utf-8")
        print(f"产物：{jp}\n产物：{mp}", file=sys.stderr)

    if args.json:
        print(json.dumps({"summary": s, "drift": dr, "sources": rows},
                         ensure_ascii=False, indent=2, default=str))
        return 0

    print(f"{s['_units']} 源：manifest {s['_manifest']} / 亮着 {s['_lit']}"
          f" / 抽取器 {s['_extractor']} / RUNNERS {s['_runner']}")
    print(f"L3：archived {s.get('archived', 0)} / stale {s.get('stale', 0)}"
          f" / unreachable {s.get('unreachable', 0)} / unmeasured {s.get('unmeasured', 0)}")
    print(f"漂移：登记指向不存在 {len(dr['registered_but_absent'])}"
          f" / 仓里有但未登记 {len(dr['in_repo_not_registered'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
