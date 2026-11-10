"""一周误报率观察表：每天记两个可对的数，而不是感觉。

判据必须能被构造触发——否则"永远是 0"看起来像健康，其实什么都没测。
本测试就钉这一点：60 天没跑必须报 25 天超窗；10 天没跑必须不报；从没成功过的源不报。
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "min_rows_observation.py"


def load():
    spec = importlib.util.spec_from_file_location("obs", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    sys.modules["obs"] = m
    assert spec.loader is not None
    spec.loader.exec_module(m)
    return m


class OverWindowTests(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

    def rec(self, days_ago: float, status="success"):
        return {"source": "demo", "status": status, "rows": 100,
                "started_at": (self.now - timedelta(days=days_ago)).isoformat()}

    def test_overdue_source_is_reported(self):
        got = self.m.over_window_days([self.rec(60)], {"demo": 25}, self.now, 35 * 24)
        self.assertEqual(got, {"demo": 25})

    def test_within_window_is_not_reported(self):
        got = self.m.over_window_days([self.rec(10)], {"demo": 25}, self.now, 35 * 24)
        self.assertEqual(got, {})

    def test_never_succeeded_is_unknown_not_overdue(self):
        """没成功过 = 不知道，不该冒充超期（否则等于编一个天数）。"""
        self.assertEqual(self.m.over_window_days([], {"demo": 25}, self.now, 35 * 24), {})

    def test_partial_counts_as_reached(self):
        """partial 是"跑到了"，用它算超窗不能被当成没跑。"""
        got = self.m.over_window_days([self.rec(10, status="partial")], {"demo": 25},
                                      self.now, 35 * 24)
        self.assertEqual(got, {})

    def test_undeclared_source_is_ignored(self):
        got = self.m.over_window_days([self.rec(60)], {}, self.now, 35 * 24)
        self.assertEqual(got, {})


class Rule1cTicketTests(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.tmp = Path(tempfile.mkdtemp(prefix="obs-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def ticket(self, name: str, source: str, note: str):
        (self.tmp / name).write_text(yaml.safe_dump(
            {"source": source, "evidence": {"notes": note}}), encoding="utf-8")

    def test_only_rule1c_of_that_day(self):
        self.ticket("20261110-aaa-1111.yaml", "aaa", "rule1c:success-but-too-few-rows")
        self.ticket("20261110-bbb-2222.yaml", "bbb", "rule3:anti-bot")
        self.ticket("20261109-ccc-3333.yaml", "ccc", "rule1c:x")
        self.assertEqual(self.m.rule1c_tickets(self.tmp, "2026-11-10"), ["aaa"])


class AppendTests(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.tmp = Path(tempfile.mkdtemp(prefix="obs-log-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.log = self.tmp / "log.md"

    def test_append_creates_header_then_row(self):
        self.log.write_text(self.m.HEADER, encoding="utf-8")
        row = self.m.build_row("2026-11-10", 118, [], {}, "month(840h)")
        self.log.write_text(self.m.HEADER + row + "\n", encoding="utf-8")
        body = self.log.read_text(encoding="utf-8")
        self.assertIn("| 日期 |", body)
        self.assertIn("| 2026-11-10 | 118 | 0 | — | — |", body)

    def test_build_row_lists_sources(self):
        row = self.m.build_row("2026-11-10", 100, ["cisa", "gb-standards"],
                               {"cisa": 3}, "month")
        self.assertIn("`cisa`", row)
        self.assertIn("`cisa`:3", row)


if __name__ == "__main__":
    unittest.main()
