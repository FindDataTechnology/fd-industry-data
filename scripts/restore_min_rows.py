#!/usr/bin/env python3
"""恢复被暂撤的 `min_rows_per_run` 声明。

背景
----
2026-11-10 处置一起误报：`boxofficemojo` / `cisa` / `gb-standards` 的"最近一次可比成功"
是 11-06 09:40–10:20 的一次性批量探针（48 源各一次、恰好 20 行、`image_tag=unknown`，
非 k8s 调度），不是源低产。三张单已 closed，三个源的声明**暂撤**，manifest 里留了三行
注释说明原因与恢复日期。

**只关单不够**：关单是终态，下一轮巡检会为同一条件重新开单（已实测复现）。所以撤了声明。
但声明不能永远撤着——它们 `data_period: month`，下次真实运行分别在 11-12 / 11-15 / 11-17，
届时"最新可比成功"才有意义。

为什么要有这个脚本
------------------
原计划是"恢复动作留给人"。但那种活没人会记得，而忘记的后果是**三个源永久失去下限守卫**
（静默降级）。把恢复做成一条命令，且幂等、可干跑、能校验前置条件，才是真的闭环。

判定前置条件（不满足就不恢复）
------------------------------
`--require-fresh-run`：只有当该源在暂撤日之后**出现过新的成功运行**时才恢复。
这是关键——若源还没重跑，"最新可比成功"仍是那条探针，恢复了会立刻重新误报。

用法
----
    python3 scripts/restore_min_rows.py                    # 干跑，列出每个源该不该恢复
    python3 scripts/restore_min_rows.py --apply            # 真写
    python3 scripts/restore_min_rows.py --snapshot /tmp/tel.json --apply   # 用离线遥测判前置
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPIDERS = REPO / "spiders"

#: 暂撤的**时刻**（工单 closed 那一瞬，UTC）。晚于它的成功运行才算"重新跑过"。
#:
#: 用完整时刻而不是日期：撤除发生在 11-10 14:22 CST 前后，若当天稍晚有人手动跑了一次
#: 验证，那是**撤除之后**的有效运行、足以支撑恢复；拿日期比（`at[:10] <= "2026-11-10"`）
#: 会把当天全部排除，导致恢复被无故拒绝——一个只在"当天跑过"时才现形的静默阻塞。
WITHDRAWN_AT = "2026-11-10T06:22:44+00:00"

#: source -> 恢复时写回的那一行（原值取自 2026-11-10 撤除时的注释）
RESTORE: dict[str, str] = {
    "boxofficemojo": "min_rows_per_run: 25    # runs=6 p10=60 p50=100; p10=60，取 25"
                     " — 见 docs/min-rows-coverage-baseline.md §3",
    "cisa": "min_rows_per_run: 25    # runs=6 p10=60 p50=100; p10=60，取 25"
            " — 见 docs/min-rows-coverage-baseline.md §3",
    "gb-standards": "min_rows_per_run: 30    # runs=7 p10=68 p50=100; p10=68，取 30 留出浮动余量"
                    " — 见 docs/min-rows-coverage-baseline.md §3",
}

_WITHDRAWN_NOTE = re.compile(
    r"^# min_rows_per_run 暂撤（\d{4}-\d{2}-\d{2}）.*?\n"
    r"(?:^#.*\n)*",
    re.MULTILINE,
)


def fresh_success_since(records: list[dict], source: str) -> dict | None:
    """该源在暂撤日之后的最新一次成功/partial 运行（没有则 None）。"""
    best = None
    for r in records:
        if r.get("source") != source:
            continue
        if r.get("status") not in ("success", "partial"):
            continue
        at = str(r.get("started_at") or "")
        if at <= WITHDRAWN_AT:      # 两个都是定长 ISO8601 前缀，字典序即时间序
            continue
        if best is None or at > str(best.get("started_at")):
            best = r
    return best


def plan(records: list[dict] | None, require_fresh: bool) -> tuple[list[tuple[Path, str, str]], list[str]]:
    """(edits, notes)。edits 项为 (manifest, 原文, 新文)。"""
    edits: list[tuple[Path, str, str]] = []
    notes: list[str] = []
    for source, line in sorted(RESTORE.items()):
        manifest = SPIDERS / source / "manifest.yaml"
        if not manifest.is_file():
            notes.append(f"{source}: manifest 不存在，跳过")
            continue
        text = manifest.read_text(encoding="utf-8")
        if re.search(r"^min_rows_per_run:", text, re.MULTILINE):
            notes.append(f"{source}: 已声明，无需恢复")
            continue
        if not _WITHDRAWN_NOTE.search(text):
            notes.append(f"{source}: 找不到暂撤注释，拒绝自动恢复（可能被人改动过）")
            continue
        if require_fresh:
            if records is None:
                notes.append(f"{source}: 未提供遥测，无法确认是否已重跑，跳过")
                continue
            fresh = fresh_success_since(records, source)
            if fresh is None:
                notes.append(f"{source}: 尚未重跑（无 {WITHDRAWN_AT} 之后的成功运行），"
                             f"保持暂撤——此时恢复会立刻重新误报")
                continue
            notes.append(f"{source}: 已重跑 {str(fresh.get('started_at'))[:16]} "
                         f"rows={fresh.get('rows')}，可恢复")
        new_text = _WITHDRAWN_NOTE.sub("", text, count=1)
        # 插回 data_period 之后（与 declare_min_rows.py 同锚点，保持文件形状一致）
        m = re.search(r"^data_period:.*$", new_text, re.MULTILINE)
        if m is None:
            notes.append(f"{source}: 找不到 data_period 锚点，拒绝自动恢复")
            continue
        at = m.end()
        sep = "" if new_text[:at].endswith("\n") else "\n"
        new_text = new_text[:at] + sep + line + new_text[at:]
        edits.append((manifest, text, new_text))
    return edits, notes


def apply_edits(edits: list[tuple[Path, str, str]]) -> tuple[list[Path], list[Path]]:
    """写前重读，变了就跳过（共享工作树）。"""
    written, skipped = [], []
    for manifest, original, new_text in edits:
        if manifest.read_text(encoding="utf-8") != original:
            skipped.append(manifest)
            continue
        manifest.write_text(new_text, encoding="utf-8")
        written.append(manifest)
    return written, skipped


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="真的写入（默认干跑）")
    ap.add_argument("--snapshot", help="遥测快照 JSON（用于判断是否已重跑）")
    ap.add_argument("--no-require-fresh-run", dest="require_fresh", action="store_false",
                    help="不检查是否已重跑（仅在已知会重跑时使用，风险自负）")
    ap.set_defaults(require_fresh=True)
    args = ap.parse_args(argv)

    records = None
    if args.snapshot:
        records = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))

    edits, notes = plan(records, args.require_fresh)
    for n in notes:
        print(f"  {n}")
    print(f"\n可恢复 {len(edits)} 个源")
    if not edits:
        return 0
    if not args.apply:
        path, _o, new = edits[0]
        added = [l for l in new.splitlines() if l.startswith("min_rows_per_run:")]
        print(f"\n干跑预览（{path.relative_to(REPO)}）:\n  + {added[0]}")
        print(f"\n其余 {len(edits)-1} 个同形状。加 --apply 落地。")
        return 0
    written, skipped = apply_edits(edits)
    for p in written:
        print(f"  wrote {p.relative_to(REPO)}")
    for p in skipped:
        print(f"  skip {p.relative_to(REPO)}: 期间被改动，未覆盖")
    print(f"\n已恢复 {len(written)} 个。建议随后跑 "
          f"`python3 scripts/validate_manifests.py` 与 "
          f"`scripts/min_rows_false_positive_report.py` 复核。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
