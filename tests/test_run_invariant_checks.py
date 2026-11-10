"""三条不变量的每晚自检：只写「待复核候选」，绝不直接写误报。

判据的要点全在"候选 ≠ 误报"这一条上：
- 用例失败可能是真回归（有人删了 --from-pg），也可能是合法重构导致的文本断言误报；
- 机器分不出，所以只能记候选；误报计数只认已复核条目，
  否则一次真回归就能把「≥2 次换行为断言」提前触发。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "run_invariant_checks.py"


def load():
    spec = importlib.util.spec_from_file_location("ric", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    sys.modules["ric"] = m
    assert spec.loader is not None
    spec.loader.exec_module(m)
    return m


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.tmp = Path(tempfile.mkdtemp(prefix="ric-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.orig = self.m.CANDIDATES
        self.m.CANDIDATES = self.tmp / "cand.jsonl"

    def tearDown(self):
        self.m.CANDIDATES = self.orig

    def lines(self):
        p = self.m.CANDIDATES
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] \
            if p.is_file() else []

    def test_failure_writes_unreviewed_candidate(self):
        written = self.m.record_candidates(["test_must_use_from_pg"], "")
        self.assertEqual(len(written), 1)
        rec = self.lines()[0]
        self.assertEqual(rec["invariant"], "from_pg")
        self.assertEqual(rec["status"], "unreviewed", "只能是候选，不能直接算误报")

    def test_same_day_same_invariant_is_idempotent(self):
        self.m.record_candidates(["test_must_use_from_pg"], "")
        again = self.m.record_candidates(["test_must_use_from_pg"], "")
        self.assertEqual(again, [], "同一天同一不变量不该重复记")
        self.assertEqual(len(self.lines()), 1)

    def test_different_invariants_both_recorded(self):
        written = self.m.record_candidates(
            ["test_must_use_from_pg", "test_landing_gate_stages_the_log"], "")
        self.assertEqual({w["invariant"] for w in written}, {"from_pg", "gate_stages_log"})

    def test_method_map_covers_every_invariant(self):
        """映射表必须把三条都覆盖，否则某条失败时会被归错堆甚至漏记。"""
        self.assertEqual(set(self.m.METHOD_TO_INVARIANT.values()),
                         {"from_pg", "before_gate", "gate_stages_log"})


class CounterSeesCandidatesTests(unittest.TestCase):
    """计数脚本要把候选单独列出来，且**不计入**误报。"""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "ifp", REPO / "scripts" / "invariant_fp_count.py")
        self.m = importlib.util.module_from_spec(spec)
        sys.modules["ifp"] = self.m
        spec.loader.exec_module(self.m)
        self.tmp = Path(tempfile.mkdtemp(prefix="ifp-cand-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.orig = self.m.CANDIDATES

    def tearDown(self):
        self.m.CANDIDATES = self.orig

    def test_candidates_listed_but_not_counted(self):
        cand = self.tmp / "cand.jsonl"
        cand.write_text(json.dumps({"date": "2026-11-12", "invariant": "from_pg",
                                    "test": "test_must_use_from_pg",
                                    "status": "unreviewed"}) + "\n", encoding="utf-8")
        self.m.CANDIDATES = cand
        ledger = {"window_start": "2026-11-11", "horizon_days": 21,
                  "switch_threshold": 2, "entries": []}
        rows = self.m.summarise(ledger, __import__("datetime").date(2026, 11, 12))
        self.assertEqual(next(r["false_positives"] for r in rows
                              if r["invariant"] == "from_pg"), 0,
                         "候选不能计入误报")
        self.assertEqual(len(self.m.load_candidates()), 1)

    def test_reviewed_candidates_are_ignored(self):
        cand = self.tmp / "cand.jsonl"
        cand.write_text(json.dumps({"date": "2026-11-12", "invariant": "from_pg",
                                    "status": "confirmed"}) + "\n", encoding="utf-8")
        self.m.CANDIDATES = cand
        self.assertEqual(self.m.load_candidates(), [], "已复核的候选不该再出现在待办里")


if __name__ == "__main__":
    unittest.main()
