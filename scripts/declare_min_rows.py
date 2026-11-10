#!/usr/bin/env python3
"""声明 `min_rows_per_run`。

出处：change `declare-health-min-rows`（已于 2026-11-10 归档为
`openspec/changes/archive/2026-11-10-declare-health-min-rows`，
规格落在 `openspec/specs/source-output-floor/`）。

为什么要脚本而不是手改 19 个文件
--------------------------------
1. **可重放**：这份声明要等另一会话把 `define-ingest-revision-and-coverage` 的 136 份
   manifest 改动提交之后才能落地。文本补丁会随那些改动漂移；脚本每次重新读现状。
2. **幂等**：已声明的源跳过，不重复插入，不会插两遍。
3. **带依据**：任务 2.1 的判据是「每个值旁注明 p10 / limit 的取值依据」，脚本把依据直接
   写进注释，人审的时候一眼能看到数字从哪来。

数据来源
--------
`docs/min-rows-coverage-baseline.md` §3（基于近 90 天 crawl_runs 的成功运行分布）。
阈值口径：取观测 p10；p50≈limit 的源是"每轮被上限截断"，取 limit 的 20%，而不是 p10=100。

    python3 scripts/declare_min_rows.py            # 干跑，只打印
    python3 scripts/declare_min_rows.py --apply    # 真写
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPIDERS = REPO / "spiders"

#: source -> (成功次数, p10, p50, 建议值, 取该值的理由)
#: 前四列逐字来自 baseline §3 的表格；第五列是口径（见模块 docstring）。
DECLARATIONS: dict[str, tuple[int, int, int, int, str]] = {
    "nmc-weather":      (818, 100, 100, 20, "p50=limit 截断型，取上限 20%"),
    "eia-v2":           (703, 100, 100, 20, "p50=limit 截断型，取上限 20%"),
    "kitco":            (63,    1,   1,  1, "p10=1"),
    "bls":              (47,    1,   1,  1, "p10=1"),
    "carbon-monitor":   (39,  100, 100, 20, "p50=limit 截断型，取上限 20%"),
    "zgdypf-boxoffice": (39,  100, 100, 20, "p50=limit 截断型，取上限 20%"),
    "open-meteo":       (35,  100, 100, 20, "p50=limit 截断型，取上限 20%"),
    "nasa-power":       (34,  100, 100, 20, "p50=limit 截断型，取上限 20%"),
    "wto-eping":        (34,   10,  10,  5, "p10=10，取半档向下"),
    "usgs-nwis":        (34,  100, 100, 20, "p50=limit 截断型，取上限 20%"),
    "noaa-ncei":        (33,   27,  29, 10, "p10=27，取 10 留出波动余量"),
    "noaa-swpc":        (33,    8,   8,  4, "p10=8，取半档（目录名为 noaa-swpc）"),
    "elexon-bmrs":      (32,   32,  32, 15, "p10=32，取约半档"),
    "nasa-firms":       (29,  100, 100, 20, "p50=limit 截断型，取上限 20%"),
    "gdacs":            (27,    6,   9,  3, "p10=6，取半档"),
    "entsoe-e":         (25,  100, 100, 20, "p50=limit 截断型，取上限 20%"),
    "steam-ccu":        (16,   11,  12,  5, "p10=11，取约半档"),
    "un-comtrade":      (15,    4,   5,  2, "p10=4，取半档"),
    "fao-food-price":   (10,   38, 116, 15, "p10=38，取 15 留出波动余量"),
    "hkex-disclosure":  (9,   221, 226, 50, "p10>limit，取上限 50%（上限 100）"),
    # --- 第二批：§5 SQL 复算（2026-11-09）得到的 A 桶余量 ---
    # baseline §3 只列了前 20 个；复算全表共 40 个，去掉上面 20 个后剩下这些。
    "worldbank-procnotices": (35, 100, 100, 20, "p50=limit 截断型，取上限 20%"),
    "wire-finddatatech-cloud": (31, 1, 1, 1, "p10=1"),
    "eurostat-api":     (10,   18, 100, 10, "p10=18，取 10 留出波动余量"),
    "socrata-ny":       (9,   100, 100, 20, "p50=limit 截断型，取上限 20%"),
    "usda-quickstats":  (8,    12,  12,  6, "p10=12，取半档"),
    "mohurd-yearbook":  (7,    14, 100,  7, "p10=14，取半档"),
    "imf-pcps":         (7,    68, 100, 30, "p10=68，取 30 留出浮动余量"),
    "gb-standards":     (7,    68, 100, 30, "p10=68，取 30 留出浮动余量"),
    "chinawuliu-logistics": (7, 27, 35, 10, "p10=27，取 10"),
    "moa-hog-price":    (6,    28,  36, 12, "p10=28，取 12"),
    "boxofficemojo":    (6,    60, 100, 25, "p10=60，取 25"),
    "cisa":             (6,    60, 100, 25, "p10=60，取 25"),
    "fao-empres":       (6,    46,  84, 20, "p10=46，取 20"),
    "socrata-chicago":  (6,   100, 100, 20, "p50=limit 截断型，取上限 20%"),
    "sec-edgar-facts":  (5,   748, 835, 150, "p10=748，分页累加、非 100 截断，取 20%"),
    "openchargemap":    (5,   100, 100, 20, "p50=limit 截断型，取上限 20%"),
    "amis-market":      (5,   100, 100, 20, "p50=limit 截断型，取上限 20%"),
    "jodi-gas":         (5,    52, 100, 25, "p10=52，取 25"),
    # --- 第三批：B 桶（多数有数，任务 3.1）---
    # B 桶 = 零行占比 <20%，即"多数时候有数、偶尔真的空"，仍属快照型语义，
    # 所以照样声明，只是阈值取 p10（任务 3.1 的口径），p10=0 的取下限 1。
    # 任务 3.2 要求观察一周：误报率 >20% 的源回退为不声明。
}

#: 复算里落在 A/B 桶、但**刻意不声明**的源：写下来，免得后来人以为漏了。
SKIPPED: dict[str, str] = {
    "inmetals": "`parked: true` + `enabled: false` + 无 schedule —— 巡检不覆盖停用源，"
                "声明了也不生效，等它复跑再补",
    "dispatch-smoke": "`parked: true` 的 trigger-only 探针（manifest 自述 \"Site health check, "
                      "trigger-only\"，每次固定写 1 行宿主名+时间戳）——巡检不覆盖停用源，"
                      "且它不是数据源，行数不代表产出",
    "metal-com": "`parked: true` + `enabled: false` + 无 schedule —— 同 inmetals，停用源不覆盖",
    "cninfo-operating": "**结构性不可声明**：零行占比 17%（合法空跑），任何正的行数下限都会按该比例出单。下限区分不了「本轮正常无数据」与「解析坏了」——需改用跨轮判据（N 轮中 M 轮为空）。见 docs/min-rows-calibration-analysis.md",
    "portwatch-imf": "**结构性不可声明**：零行占比 15%（合法空跑），任何正的行数下限都会按该比例出单。下限区分不了「本轮正常无数据」与「解析坏了」——需改用跨轮判据（N 轮中 M 轮为空）。见 docs/min-rows-calibration-analysis.md",
    "nbs-stats": "**结构性不可声明**：零行占比 10%（合法空跑），任何正的行数下限都会按该比例出单。下限区分不了「本轮正常无数据」与「解析坏了」——需改用跨轮判据（N 轮中 M 轮为空）。见 docs/min-rows-calibration-analysis.md",
    "cpcaauto-monthly": "**结构性不可声明**：零行占比 3%（合法空跑），任何正的行数下限都会按该比例出单。下限区分不了「本轮正常无数据」与「解析坏了」——需改用跨轮判据（N 轮中 M 轮为空）。见 docs/min-rows-calibration-analysis.md",
    "eastmoney-futures": "**结构性不可声明**：零行占比 3%（合法空跑），任何正的行数下限都会按该比例出单。下限区分不了「本轮正常无数据」与「解析坏了」——需改用跨轮判据（N 轮中 M 轮为空）。见 docs/min-rows-calibration-analysis.md",
    "eastmoney-data": "**结构性不可声明**：零行占比 2%（合法空跑），任何正的行数下限都会按该比例出单。下限区分不了「本轮正常无数据」与「解析坏了」——需改用跨轮判据（N 轮中 M 轮为空）。见 docs/min-rows-calibration-analysis.md",
    "fangchan-market": "**结构性不可声明**：零行占比 3%（合法空跑），任何正的行数下限都会按该比例出单。下限区分不了「本轮正常无数据」与「解析坏了」——需改用跨轮判据（N 轮中 M 轮为空）。见 docs/min-rows-calibration-analysis.md",
    "rmfyalk-case-crawl": "遥测源名与目录名不对应：目录只有 `spiders/rmfyalk`（`parked: true`），"
                          "没有 `spiders/rmfyalk-case-crawl/`。声明落在目录上，名对不上就不声明；"
                          "要声明得先统一源名或给它建目录",
}

FIELD = "min_rows_per_run"
#: Preferred anchor, then fallback. `data_period` wins because it carries the same
#: scheduling semantics; a single alternation pattern would instead match whichever
#: key appears first in the file, which is `name:` in most manifests — measured:
#: the first version inserted the field between `name:` and `schedule:`, splitting
#: the two lines that describe when the source runs.
_PREFERRED = re.compile(r"^data_period:.*$", re.MULTILINE)
_FALLBACK = re.compile(r"^name:.*$", re.MULTILINE)


def _existing(text: str) -> bool:
    return bool(re.search(rf"^{FIELD}:", text, re.MULTILINE))


def _shown(manifest: Path) -> str:
    """Path for humans, without throwing when the file lives outside the repo."""
    try:
        return str(manifest.relative_to(REPO))
    except ValueError:
        return str(manifest)


def _comment(source: str) -> str:
    runs, p10, p50, value, why = DECLARATIONS[source]
    # One comment, not two: a second '#' reads like a second annotation and makes the
    # line harder to scan. The provenance belongs at the end of the same sentence.
    return (
        f"{FIELD}: {value}"
        f"    # runs={runs} p10={p10} p50={p50}; {why}"
        f" — 见 docs/min-rows-coverage-baseline.md §3"
    )


def plan() -> tuple[list[tuple[Path, str, str]], list[str]]:
    """(edits, problems). Every target is verified before anything is written.

    Each edit carries the bytes read, not just the bytes to write: this tree is shared
    with other sessions, and `--apply` re-checks the file against `original` immediately
    before writing so a concurrent edit is skipped instead of silently overwritten.
    """
    edits: list[tuple[Path, str, str]] = []
    problems: list[str] = []
    for source in sorted(DECLARATIONS):
        manifest = SPIDERS / source / "manifest.yaml"
        if not manifest.exists():
            # The error path must be more robust than the happy one: a relative_to()
            # that throws here would turn "one source is missing" into a traceback,
            # which is exactly how a partial run becomes an unexplained one.
            problems.append(f"{source}: manifest 不存在（{_shown(manifest)}）")
            continue
        text = manifest.read_text(encoding="utf-8")
        if _existing(text):
            problems.append(f"{source}: 已声明，跳过")
            continue
        anchor = _PREFERRED.search(text) or _FALLBACK.search(text)
        if anchor is None:
            problems.append(f"{source}: 找不到插入锚点（data_period: / name:）")
            continue
        at = anchor.end()
        # `data_period: month` is often the LAST line and may have no trailing
        # newline. Splicing blindly concatenates the new field onto it, producing
        # `data_period: monthmin_rows_per_run: 1` — valid-looking YAML that reads as
        # one key. The separator belongs before the insert, and only when needed.
        separator = "" if text[:at].endswith("\n") else "\n"
        new_text = text[:at] + separator + _comment(source) + text[at:]
        edits.append((manifest, text, new_text))
    return edits, problems


def apply_edits(edits: list[tuple[Path, str, str]]) -> tuple[list[Path], list[Path]]:
    """Write each edit only if the file still holds the bytes we planned against.

    Returns (written, skipped). Split out from `main` so the concurrency guard is
    testable without capturing stdout: the guard is the whole reason this script
    exists rather than a one-shot patch.
    """
    written: list[Path] = []
    skipped: list[Path] = []
    for manifest, original, new_text in edits:
        # The tree may be shared with another session that just committed its own batch:
        # overwriting blindly would drop whatever it wrote between our read and our write.
        if manifest.read_text(encoding="utf-8") != original:
            skipped.append(manifest)
            continue
        manifest.write_text(new_text, encoding="utf-8")
        written.append(manifest)
    return written, skipped


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="真的写入（默认只干跑）")
    args = ap.parse_args(argv)

    edits, problems = plan()
    for line in problems:
        print(f"  skip {line}")
    print(f"\n可声明 {len(edits)} 个源")

    if not edits:
        return 0

    if not args.apply:
        manifest, _original, new_text = edits[0]
        added = [ln for ln in new_text.splitlines() if ln.startswith(FIELD)]
        print(f"\n干跑预览（{manifest.relative_to(REPO)}）:")
        print(f"  + {added[0]}")
        print(f"\n其余 {len(edits) - 1} 个同形状。加 --apply 落地。")
        return 0

    written, skipped = apply_edits(edits)
    for manifest in written:
        print(f"  wrote {_shown(manifest)}")
    for manifest in skipped:
        print(f"  skip {_shown(manifest)}: 期间被其他会话改动，未覆盖")
    print(f"\n已写入 {len(written)} 个 manifest。建议随后跑 validate_manifests.py。")
    if skipped:
        print(f"{len(skipped)} 个因并发改动被跳过，重跑一次即可补上。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
