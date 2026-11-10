#!/usr/bin/env python3
"""三条结构不变量的误报账本：算计数、给判定。

为什么要这个东西
----------------
`tests/test_health_workflow_summary.py` 里钉了三条**文本/结构**断言（带 `--from-pg`、
排在落库门前、落库门确实 `git add` 了观察表）。合法重构会让它们误报，所以当初约定：
**累计到"三周零误报"或"≥2 次误报"时回检，决定是否换成行为断言。**

约定容易忘，所以计数与判定都做成可执行的：账本 `docs/invariant-fp-ledger.json` 里
手动登记每次误报（误报是判断，不是机器能自己判的），本脚本读账本给结论。

前提必须先说清（2026-11-11 核实）
----------------------------------
**每晚巡检不跑单测**：`health-inspect.yml` 里没有 pytest 步骤，`runner/Dockerfile`
也只装了 `python3` + `python3-yaml`，没有 pytest。所以"连续产出 N 天"**不会**自动
攒出误报计数——计数只能来自用例真正跑过并且失败、且经人判定为误报的场合
（本地全量回归、或别的仓的 CI）。本脚本因此不扫日志，只读账本。

用法
----
    python3 scripts/invariant_fp_count.py                 # 表格 + 判定
    python3 scripts/invariant_fp_count.py --json          # 机器可读
    python3 scripts/invariant_fp_count.py --now 2026-12-05   # 假装到了那天（回检演练）

退出码：0 = 继续观察 / 维持文本断言；3 = 有条目达到"该换行为断言"的门槛。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

LEDGER = REPO / "docs" / "invariant-fp-ledger.json"
#: 巡检每晚可能写进来的**待复核候选**（不是误报，只是"用例失败了，请判一下"）
CANDIDATES = REPO / "docs" / "invariant-fp-candidates.jsonl"

#: 三条不变量的稳定标识（账本里用这些名字登记，别用测试函数名——改个名就断）
INVARIANTS = {
    "from_pg": "观察步骤必须带 --from-pg",
    "before_gate": "观察步骤必须排在落库门前",
    "gate_stages_log": "落库门必须 git add 观察表",
}

OBSERVING = "observing"
KEEP_TEXTUAL = "keep_textual"
SWITCH_BEHAVIORAL = "switch_behavioral"


def load_ledger(path: Path = LEDGER) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_candidates(path: Path | None = None) -> list[dict]:
    """待复核候选（巡检写的那份）。读不到就当没有——它不是判据，只是待办。

    `path` 默认值写成 None 而不是 `= CANDIDATES`：默认参数在**定义时**求值，
    直接写 `= CANDIDATES` 会让用例把模块常量替换成临时文件后仍读旧路径
    （2026-11-11 实测踩到，断言 0 != 1）。
    """
    path = CANDIDATES if path is None else path
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return [c for c in out if c.get("status", "unreviewed") == "unreviewed"]


def summarise(ledger: dict, now: date) -> list[dict]:
    """每个不变量一条：误报次数、窗口剩余天数、判定。"""
    start = date.fromisoformat(ledger["window_start"])
    horizon = start + timedelta(days=int(ledger["horizon_days"]))
    threshold = int(ledger["switch_threshold"])

    counts: dict[str, int] = defaultdict(int)
    last: dict[str, str] = {}
    unknown: list[str] = []
    for e in ledger.get("entries") or []:
        key = str(e.get("invariant") or "")
        if key not in INVARIANTS:
            unknown.append(key)
            continue
        counts[key] += 1
        d = str(e.get("date") or "")
        if d > last.get(key, ""):
            last[key] = d

    rows: list[dict] = []
    for key, desc in INVARIANTS.items():
        n = counts[key]
        if n >= threshold:
            verdict = SWITCH_BEHAVIORAL
        elif now >= horizon and n == 0:
            verdict = KEEP_TEXTUAL
        else:
            verdict = OBSERVING
        rows.append({
            "invariant": key,
            "desc": desc,
            "false_positives": n,
            "last_at": last.get(key),
            "verdict": verdict,
            "days_left": max(0, (horizon - now).days),
        })
    if unknown:
        # 拼错的不变量名会让计数静默偏小，所以要显式报出来，不能吞
        raise SystemExit(f"账本里有无法识别的不变量名：{sorted(set(unknown))}；"
                         f"合法值：{sorted(INVARIANTS)}")
    return rows


def render(rows: list[dict], ledger: dict, now: date) -> str:
    start = date.fromisoformat(ledger["window_start"])
    horizon = start + timedelta(days=int(ledger["horizon_days"]))
    lines = [f"观察窗口 {ledger['window_start']} → {horizon.isoformat()}"
             f"（{ledger['horizon_days']} 天，判定日 {now.isoformat()}）",
             f"门槛：误报 ≥ {ledger['switch_threshold']} 次 → 换行为断言；满窗零误报 → 维持文本断言",
             "",
             f"{'不变量':28} {'误报':>4} {'最近':>12} {'剩余天':>6}  判定",
             "-" * 76]
    label = {OBSERVING: "继续观察", KEEP_TEXTUAL: "维持文本断言",
             SWITCH_BEHAVIORAL: "该换行为断言"}
    for r in rows:
        lines.append(f"{r['invariant']:28} {r['false_positives']:>4} "
                     f"{r['last_at'] or '—':>12} {r['days_left']:>6}  "
                     f"{label[r['verdict']]}")
    pend = load_candidates()
    if pend:
        lines += ["", f"待复核候选 {len(pend)} 条（**不计入误报**，需人判定是真回归还是误报）："]
        for c in pend:
            lines.append(f"  {c.get('date')}  {c.get('invariant')}  {c.get('test', '')}")
        lines.append("  确认是误报 → 登记进 docs/invariant-fp-ledger.json 的 entries，并从这里删掉该行")
    if not (ledger.get("entries") or []):
        lines += ["", "账本为空：还没有登记过误报。（空账本 = 零误报，不是「没在观察」。）"]
    lines += ["", "登记方式：误报是人工判断——在 docs/invariant-fp-ledger.json 的 entries 里"
                  "加一条 {date, invariant, why, evidence}。"]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--now", help="覆盖今天（YYYY-MM-DD，回检演练用）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    ledger = load_ledger(Path(args.ledger))
    now = date.fromisoformat(args.now) if args.now else date.today()
    rows = summarise(ledger, now)

    if args.json:
        print(json.dumps({"now": now.isoformat(), "rows": rows}, ensure_ascii=False, indent=2))
    else:
        print(render(rows, ledger, now))
    return 3 if any(r["verdict"] == SWITCH_BEHAVIORAL for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
