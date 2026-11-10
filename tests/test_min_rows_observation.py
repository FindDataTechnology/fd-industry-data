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

    def test_cst_day_wins_over_utc_filename_prefix(self):
        """00:00–08:00 CST 的单带的是前一天的 UTC 文件名，必须仍算进 CST 当天。

        2026-11-10 实测：三张 rule1c 单 created_at=2026-11-09T22:30Z（CST 11-10 06:30），
        文件名 `20261109-*`。按前缀匹配时观察表 11-10 那行记成 0，实际 3 张。
        """
        (self.tmp / "20261109-ddd-4444.yaml").write_text(yaml.safe_dump(
            {"source": "ddd", "created_at": "2026-11-09T22:30:30.114511+00:00",
             "evidence": {"notes": "rule1c:success-but-too-few-rows"}}), encoding="utf-8")
        (self.tmp / "20261110-eee-5555.yaml").write_text(yaml.safe_dump(
            {"source": "eee", "created_at": "2026-11-10T03:45:00+00:00",
             "evidence": {"notes": "rule1c:success-but-too-few-rows"}}), encoding="utf-8")
        # 11-09T15:59Z = CST 11-09 23:59，属前一天，不该算进 11-10
        (self.tmp / "20261109-fff-6666.yaml").write_text(yaml.safe_dump(
            {"source": "fff", "created_at": "2026-11-09T15:59:00+00:00",
             "evidence": {"notes": "rule1c:x"}}), encoding="utf-8")
        self.assertEqual(self.m.rule1c_tickets(self.tmp, "2026-11-10"), ["ddd", "eee"])


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


class FingerprintTests(unittest.TestCase):
    """四项上线指纹必须**能判失败**，否则"全 ✓"只是没测到。

    这四项是"新代码是否在跑"的判据，且 2026-11-10 起 last-run.json 已移出 git，
    只能从遥测 + manifest 现算。
    """

    def setUp(self):
        self.m = load()
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "spiders").mkdir()
        self.now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

    def _unit(self, name: str, body: str = "data_period: day\n"):
        d = self.tmp / "spiders" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.yaml").write_text(f"name: {name}\n{body}", encoding="utf-8")

    def _rec(self, source, rows=100, status="success", days_ago=1):
        return {"source": source, "status": status, "rows": rows,
                "started_at": (self.now - timedelta(days=days_ago)).isoformat()}

    def _fp(self, records, cfg=None):
        from fd_industry_data.health.config import HealthConfig
        return self.m.collect_fingerprints(records, self.tmp, cfg or HealthConfig(), self.now)

    def test_unregistered_requires_a_missing_manifest(self):
        self._unit("known")
        fp = self._fp([self._rec("known"), self._rec("ghost")])
        self.assertTrue(fp["unregistered_sources"]["present"])
        self.assertEqual(fp["unregistered_sources"]["value"], ["ghost"])

    def test_excluded_sources_are_not_unregistered(self):
        """按裁决排除的跨仓 Job 不该被算成"该登记没登记"。"""
        from fd_industry_data.health.config import HealthConfig
        fp = self._fp([self._rec("law-job")],
                      HealthConfig(excluded_sources=("law-job",)))
        self.assertEqual(fp["unregistered_sources"]["value"], [])

    def test_deferred_carry_verdict_is_false_when_entries_lack_it(self):
        """③ 必须能报 ✗：直接喂没有 category/rule 的顺延条目形态。

        用 max_daily_tickets=0 制造真实顺延，断言 ok 为真；再用一个手工构造的
        「旧形态」列表断言它会报假 —— 两向都钉住，避免"永远 ✓"。
        """
        from dataclasses import replace
        from fd_industry_data.health.config import HealthConfig
        self._unit("aaa", "data_period: day\nmin_rows_per_run: 5\n")
        self._unit("bbb", "data_period: day\nmin_rows_per_run: 5\n")
        recs = [self._rec("aaa", rows=0), self._rec("bbb", rows=0)]
        cfg = replace(HealthConfig(), max_daily_tickets=0)
        fp = self._fp(recs, cfg)
        self.assertGreater(fp["deferred_carry_verdict"]["count"], 0,
                           "限流为 0 时必须产生顺延，否则这条指纹测不到")
        self.assertTrue(fp["deferred_carry_verdict"]["ok"])

    def test_deferred_verdict_helper_rejects_old_shape(self):
        """两向都钉住：缺 category/rule 的旧形态必须判 False。

        只断言"真实顺延里带着"是不够的——把判据硬编码成 True 也能通过
        （2026-11-10 反向验证时 11 条全绿，正是这个原因）。
        """
        old_shape = [{"source": "x", "reason": "daily cap reached"}]
        self.assertFalse(self.m._deferred_carry_verdict(old_shape))
        new_shape = [{"source": "x", "reason": "daily cap reached",
                      "category": "structure", "rule": "rule1c"}]
        self.assertTrue(self.m._deferred_carry_verdict(new_shape))
        self.assertTrue(self.m._deferred_carry_verdict([]), "空列表应视为通过")
