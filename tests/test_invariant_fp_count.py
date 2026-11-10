"""不变量误报账本：判定规则必须两向都能触发。

规则只有三条，但每条都有边界，边界错一格就会得出相反结论：
  满窗 + 零误报 → 维持文本断言（不是"继续观察"）
  未满窗 + 零误报 → 继续观察（不是"维持"）
  ≥2 次 → 换行为断言，且**不受窗口是否到期影响**（攒够两次就该换，不用等满窗）
另外账本里写错不变量名会让计数静默偏小，必须炸出来而不是当 0。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "invariant_fp_count.py"


def load():
    spec = importlib.util.spec_from_file_location("ifp", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    sys.modules["ifp"] = m
    assert spec.loader is not None
    spec.loader.exec_module(m)
    return m


def ledger(entries=None, **over) -> dict:
    base = {"window_start": "2026-11-11", "horizon_days": 21, "switch_threshold": 2,
            "entries": entries or []}
    base.update(over)
    return base


def verdict_of(rows, key):
    return next(r["verdict"] for r in rows if r["invariant"] == key)


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_zero_before_horizon_is_observing(self):
        rows = self.m.summarise(ledger(), date(2026, 11, 20))
        self.assertEqual(verdict_of(rows, "from_pg"), self.m.OBSERVING)
        self.assertEqual(next(r["days_left"] for r in rows if r["invariant"] == "from_pg"), 12)

    def test_zero_on_horizon_is_keep_textual(self):
        """到判定日（window_start + 21 天）当天就该回检，不必再多等一天。"""
        rows = self.m.summarise(ledger(), date(2026, 12, 2))
        self.assertEqual(verdict_of(rows, "from_pg"), self.m.KEEP_TEXTUAL)
        self.assertEqual(next(r["days_left"] for r in rows if r["invariant"] == "from_pg"), 0)

    def test_zero_after_horizon_is_still_keep(self):
        rows = self.m.summarise(ledger(), date(2026, 12, 20))
        self.assertEqual(verdict_of(rows, "from_pg"), self.m.KEEP_TEXTUAL)

    def test_two_hits_switch_even_before_horizon(self):
        """攒够两次就该换，不用等满窗——这是"取严的一侧"。"""
        e = [{"date": "2026-11-15", "invariant": "from_pg"},
             {"date": "2026-11-16", "invariant": "from_pg"}]
        rows = self.m.summarise(ledger(e), date(2026, 11, 17))
        self.assertEqual(verdict_of(rows, "from_pg"), self.m.SWITCH_BEHAVIORAL)

    def test_one_hit_is_still_observing(self):
        e = [{"date": "2026-11-15", "invariant": "from_pg"}]
        rows = self.m.summarise(ledger(e), date(2026, 11, 17))
        self.assertEqual(verdict_of(rows, "from_pg"), self.m.OBSERVING)

    def test_counts_are_per_invariant(self):
        e = [{"date": "2026-11-15", "invariant": "from_pg"},
             {"date": "2026-11-16", "invariant": "before_gate"}]
        rows = self.m.summarise(ledger(e), date(2026, 11, 17))
        self.assertEqual(verdict_of(rows, "from_pg"), self.m.OBSERVING)
        self.assertEqual(verdict_of(rows, "before_gate"), self.m.OBSERVING)
        self.assertEqual(verdict_of(rows, "gate_stages_log"), self.m.OBSERVING)

    def test_last_at_is_the_latest_date(self):
        e = [{"date": "2026-11-15", "invariant": "from_pg"},
             {"date": "2026-11-19", "invariant": "from_pg"},
             {"date": "2026-11-17", "invariant": "from_pg"}]
        rows = self.m.summarise(ledger(e), date(2026, 11, 20))
        self.assertEqual(next(r["last_at"] for r in rows if r["invariant"] == "from_pg"),
                         "2026-11-19")

    def test_unknown_invariant_name_blows_up(self):
        """拼错名字会让计数偏小——必须炸，不能静默算成 0。"""
        e = [{"date": "2026-11-15", "invariant": "from_pg_typo"}]
        with self.assertRaises(SystemExit):
            self.m.summarise(ledger(e), date(2026, 11, 17))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.tmp = Path(tempfile.mkdtemp(prefix="ifp-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, doc) -> str:
        p = self.tmp / "l.json"
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return str(p)

    def test_exit_zero_while_observing(self):
        rc = self.m.main(["--ledger", self.write(ledger()), "--now", "2026-11-20"])
        self.assertEqual(rc, 0)

    def test_exit_three_when_switch_needed(self):
        e = [{"date": "2026-11-15", "invariant": "gate_stages_log"},
             {"date": "2026-11-16", "invariant": "gate_stages_log"}]
        rc = self.m.main(["--ledger", self.write(ledger(e)), "--now", "2026-11-17"])
        self.assertEqual(rc, 3)

    def test_repo_ledger_is_valid_and_starts_empty(self):
        """仓里那份账本必须能解析，且当前应无条目（观察刚开始）。"""
        doc = self.m.load_ledger()
        self.assertEqual(doc["entries"], [])
        self.assertEqual(doc["window_start"], "2026-11-11")
        self.assertIn("不跑单测", doc["premise"], "前提必须写在账本里，别只写在文档里")


if __name__ == "__main__":
    unittest.main()
