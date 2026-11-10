"""三源窗口态：六态都要能被构造触发，且"通过"只能有一种。

判据是「窗口之后新建的那次运行」——这里钉四件最容易做错的事：
① 窗口前的成功不能算；② 跑成功但行数低于 floor 必须红（那是当初三张误报单的形态）；
③ 窗口未到不能报红；④ 日期字面量只能存在一处。
"""

from __future__ import annotations

import pathlib
import unittest
from datetime import datetime, timedelta, timezone

from fd_industry_data.health.withdrawn import CST, WITHDRAWN, window_states

WIN = {w.source: w for w in WITHDRAWN}
GB = WIN["gb-standards"]          # 窗口 2026-11-12 02:25 CST，floor 30


def rec(status: str, started: datetime, rows=None, source: str = "gb-standards") -> dict:
    return {"source": source, "status": status,
            "started_at": started.isoformat(),
            "rows": rows}


class WindowStateTests(unittest.TestCase):
    def setUp(self):
        self.before = GB.at - timedelta(hours=6)      # 窗口之前
        self.after = GB.at + timedelta(hours=1)       # 窗口之后
        self.now_pre = GB.at - timedelta(hours=2)     # 窗口还没到
        self.now_post = GB.at + timedelta(hours=12)

    def only_gb(self, records, now):
        rows = window_states(records, now, windows=(GB,))
        return rows[0]

    # A1 四态可达
    def test_no_record_after_window(self):
        r = self.only_gb([rec("success", self.before, 100)], self.now_post)
        self.assertEqual(r["verdict"], "no_new_job")
        self.assertTrue(r["red"])

    def test_failed_after_window(self):
        r = self.only_gb([rec("failed", self.after, 0)], self.now_post)
        self.assertEqual(r["verdict"], "failed")
        self.assertTrue(r["red"])

    def test_running_after_window(self):
        r = self.only_gb([rec("running", self.after)], self.now_post)
        self.assertEqual(r["verdict"], "incomplete")
        self.assertTrue(r["red"])

    def test_complete(self):
        r = self.only_gb([rec("success", self.after, 100)], self.now_post)
        self.assertEqual(r["verdict"], "complete")
        self.assertFalse(r["red"])

    # A2 low_rows 必须红
    def test_low_rows_is_red_not_complete(self):
        r = self.only_gb([rec("success", self.after, 20)], self.now_post)
        self.assertEqual(r["verdict"], "low_rows", "rows=20 < floor 30，不能判成 complete")
        self.assertTrue(r["red"])

    def test_rows_missing_counts_as_low(self):
        """rows 缺失（None）按 0 算：读不到不能当成达标。"""
        r = self.only_gb([rec("success", self.after, None)], self.now_post)
        self.assertEqual(r["verdict"], "low_rows")

    # A3 窗口前的成功不算
    def test_success_before_window_does_not_count(self):
        r = self.only_gb([rec("success", self.before, 500)], self.now_post)
        self.assertEqual(r["verdict"], "no_new_job")
        self.assertIsNotNone(r["last_success"], "no_new_job 时要带出最近成功供人判断")

    # A4 边界按 >=
    def test_exact_window_instant_counts(self):
        r = self.only_gb([rec("success", GB.at, 100)], self.now_post)
        self.assertEqual(r["verdict"], "complete")

    # pending 不报红
    def test_pending_is_not_red(self):
        r = self.only_gb([], self.now_pre)
        self.assertEqual(r["verdict"], "pending")
        self.assertFalse(r["red"], "窗口未到提前报红＝天天误报")

    def test_other_sources_ignored(self):
        r = self.only_gb([rec("success", self.after, 100, source="cisa")], self.now_post)
        self.assertEqual(r["verdict"], "no_new_job")

    def test_latest_in_window_wins(self):
        records = [rec("failed", GB.at + timedelta(hours=1)),
                   rec("success", GB.at + timedelta(hours=5), 100)]
        r = self.only_gb(records, self.now_post)
        self.assertEqual(r["verdict"], "complete")


class SingleSourceOfTruthTests(unittest.TestCase):
    """A7：窗口日期只能有一处字面量，否则两处会各自漂移。"""

    def test_k8s_checker_has_no_date_literals(self):
        import re
        p = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "check_withdrawn_sources.py"
        hits = re.findall(r"datetime\(2026, *11, *(?:12|15|17)", p.read_text(encoding="utf-8"))
        self.assertEqual(hits, [], "日期应全部来自 health/withdrawn.py")

    def test_three_windows_with_floors(self):
        self.assertEqual({w.source for w in WITHDRAWN},
                         {"gb-standards", "cisa", "boxofficemojo"})
        self.assertEqual({w.source: w.floor for w in WITHDRAWN},
                         {"gb-standards": 30, "cisa": 25, "boxofficemojo": 25},
                         "floor 必须与撤声明时的原值一致（restore_min_rows.py 的 RESTORE）")

    def test_windows_are_cst(self):
        for w in WITHDRAWN:
            self.assertEqual(w.at.utcoffset(), timedelta(hours=8))


if __name__ == "__main__":
    unittest.main()
