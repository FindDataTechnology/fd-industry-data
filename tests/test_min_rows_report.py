"""回看报表：把「出过几张行数不足单」与「出单后是否又达标」放在一起。

任务 3.2 要求观察一周统计误报率。没有这份报表，那件事只能靠翻目录，于是不会真被做。
但报表不能替人下结论——「出单后又达标」既可能是误报，也可能是真低产后恢复，
两者不划等号。测试就把这一点钉住。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

import importlib.util

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "min_rows_false_positive_report.py"


def load_module():
    # `@dataclass` 在 exec_module 期间需要能从 sys.modules 找到自己所在的模块，
    # 否则报 "'NoneType' object has no attribute '__dict__'"。所以先注册再执行。
    import sys

    spec = importlib.util.spec_from_file_location("mr_report", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["mr_report"] = m
    spec.loader.exec_module(m)
    return m


class MinRowsReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.m = load_module()
        self.tmp = Path(tempfile.mkdtemp(prefix="mr-rep-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tickets = self.tmp / "tickets"
        self.tickets.mkdir()

    def ticket(self, source: str, day: str, note: str) -> None:
        doc = {"source": source, "category": "structure", "created_at": f"{day}T05:00:00+00:00",
               "evidence": {"notes": note}}
        (self.tickets / f"{day.replace('-','')}-{source}-abc12345.yaml").write_text(
            yaml.safe_dump(doc), encoding="utf-8")

    def test_only_rule1c_tickets_are_counted(self) -> None:
        self.ticket("aaa", "2026-11-08", "rule1c:success-but-too-few-rows rows=2 < 25")
        self.ticket("bbb", "2026-11-08", "rule3:anti-bot run#1/3 http 403")
        since = self.m._parse("2026-11-01T00:00:00+00:00")
        got = self.m.load_rule1c_tickets(self.tickets, since)
        self.assertEqual([t.source for t in got], ["aaa"],
                         "category=structure 的单里也混着别的规则，不能按 category 过滤")

    def test_success_after_last_ticket_is_reported_as_a_fact(self) -> None:
        self.ticket("aaa", "2026-11-08", "rule1c:... rows=2 < 25")
        records = [
            {"source": "aaa", "status": "success", "rows": 2,
             "started_at": "2026-11-08T04:00:00+00:00"},   # 出单前（低产，触发该单）
            {"source": "aaa", "status": "success", "rows": 100,
             "started_at": "2026-11-09T04:00:00+00:00"},   # 出单后达标
        ]
        rows = self.m.build_rows(
            self.m.load_rule1c_tickets(self.tickets, self.m._parse("2026-11-01T00:00:00+00:00")),
            records, {"aaa": 25})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tickets"], 1)
        self.assertEqual(rows[0]["successes_after_last_ticket"], 1)

    def test_no_qualifying_success_afterwards_reports_zero(self) -> None:
        self.ticket("aaa", "2026-11-08", "rule1c:... rows=2 < 25")
        records = [{"source": "aaa", "status": "success", "rows": 2,
                    "started_at": "2026-11-09T04:00:00+00:00"}]
        rows = self.m.build_rows(
            self.m.load_rule1c_tickets(self.tickets, self.m._parse("2026-11-01T00:00:00+00:00")),
            records, {"aaa": 25})
        self.assertEqual(rows[0]["successes_after_last_ticket"], 0)

    def test_undeclared_source_reports_unknown_not_zero(self) -> None:
        """没有下限就没法判"达标"——必须显示未知，不能看起来像"零次达标"。"""
        self.ticket("ccc", "2026-11-08", "rule1c:... rows=2 < 25")
        rows = self.m.build_rows(
            self.m.load_rule1c_tickets(self.tickets, self.m._parse("2026-11-01T00:00:00+00:00")),
            [], {})
        self.assertIsNone(rows[0]["successes_after_last_ticket"])
        self.assertIn("未声明", self.m.render(rows, 7, 1))

    def test_partial_counts_as_a_reach_for_recovery(self) -> None:
        self.ticket("aaa", "2026-11-08", "rule1c:... rows=2 < 25")
        records = [{"source": "aaa", "status": "partial", "rows": 30,
                    "started_at": "2026-11-09T04:00:00+00:00"}]
        rows = self.m.build_rows(
            self.m.load_rule1c_tickets(self.tickets, self.m._parse("2026-11-01T00:00:00+00:00")),
            records, {"aaa": 25})
        self.assertEqual(rows[0]["successes_after_last_ticket"], 1)


if __name__ == "__main__":
    unittest.main()
