"""暂撤三源的 Job 判据：五种态都要能被构造触发。

判据是"窗口之后新建的最新 Job 必须 Complete 1/1"，难点全在边界上：
窗口前那次跑也不能顶用、Failed 与 0/1 要分开、窗口未到不能报红。
这些用例就是钉这几条——否则"永远是 pending"或"永远绿"都会看起来正常。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_withdrawn_sources.py"


def load():
    spec = importlib.util.spec_from_file_location("chk", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    sys.modules["chk"] = m
    assert spec.loader is not None
    spec.loader.exec_module(m)
    return m


def job(source: str, created: str, *, succeeded: int = 0, failed: bool = False) -> dict:
    """照 kubectl get jobs -o json 的真实形状造一条。"""
    st: dict = {}
    if succeeded:
        st["succeeded"] = succeeded
    if failed:
        st["conditions"] = [{"type": "Failed", "status": "True",
                             "reason": "BackoffLimitExceeded"}]
    return {"metadata": {"name": f"fd-industry-{source}-x",
                         "labels": {"fd-industry/source": source},
                         "creationTimestamp": created},
            "status": st}


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.m = load()
        # gb-standards 窗口 = 2026-11-12 02:25 CST = 2026-11-11T18:25Z
        self.before = "2026-11-11T00:00:00Z"   # 窗口之前
        self.after = "2026-11-11T19:00:00Z"    # 窗口之后
        self.pre = self.m._ts("2026-11-11T12:00:00Z")
        self.post = self.m._ts("2026-11-12T12:00:00Z")

    def one(self, items, now):
        rows = self.m.classify(items, {"gb-standards": self.m.WINDOWS["gb-standards"]}, now)
        return rows[0]

    def test_window_not_reached_is_pending_not_red(self):
        r = self.one([job("gb-standards", self.before, succeeded=1)], self.pre)
        self.assertEqual(r["verdict"], "pending")
        self.assertFalse(r["red"], "窗口未到提前报红＝天天误报")

    def test_complete_after_window_passes(self):
        r = self.one([job("gb-standards", self.after, succeeded=1)], self.post)
        self.assertEqual(r["verdict"], "complete")
        self.assertFalse(r["red"])

    def test_job_before_window_does_not_count(self):
        """窗口前那次 Complete 也不算——它跑的是撤声明之前的状态。"""
        r = self.one([job("gb-standards", self.before, succeeded=1)], self.post)
        self.assertEqual(r["verdict"], "no_new_job")
        self.assertTrue(r["red"])

    def test_failed_is_distinct_from_no_new_job(self):
        r = self.one([job("gb-standards", self.after, failed=True)], self.post)
        self.assertEqual(r["verdict"], "failed")
        self.assertTrue(r["red"])

    def test_zero_of_one_is_incomplete_not_failed(self):
        r = self.one([job("gb-standards", self.after)], self.post)
        self.assertEqual(r["verdict"], "incomplete")
        self.assertTrue(r["red"])

    def test_latest_in_window_wins(self):
        items = [job("gb-standards", self.after, failed=True),
                 job("gb-standards", "2026-11-11T21:00:00Z", succeeded=1)]
        r = self.one(items, self.post)
        self.assertEqual(r["verdict"], "complete", "应取窗口内最新那条")

    def test_other_sources_ignored(self):
        r = self.one([job("cisa", self.after, succeeded=1)], self.post)
        self.assertEqual(r["verdict"], "no_new_job", "别的源的 Job 不能算进来")


class MainTests(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.tmp = Path(tempfile.mkdtemp(prefix="chk-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, items):
        p = self.tmp / "jobs.json"
        p.write_text(json.dumps({"items": items}), encoding="utf-8")
        return str(p)

    def test_exit_zero_when_all_pending(self):
        rc = self.m.main(["--from-json", self.write([job("gb-standards", "2026-11-01T00:00:00Z", succeeded=1)]),
                          "--now", "2026-11-10T12:00:00+00:00"])
        self.assertEqual(rc, 0)

    def test_exit_one_when_any_red(self):
        rc = self.m.main(["--from-json", self.write([]),
                          "--now", "2026-11-20T00:00:00+00:00"])
        self.assertEqual(rc, 1, "窗口过了还没 Job → 必须非零")

    def test_json_mode_shape(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.m.main(["--from-json", self.write([]), "--json",
                         "--now", "2026-11-10T12:00:00+00:00"])
        data = json.loads(buf.getvalue())
        self.assertEqual({r["source"] for r in data["rows"]},
                         {"gb-standards", "cisa", "boxofficemojo"})
        self.assertTrue(all(r["verdict"] == "pending" for r in data["rows"]))


if __name__ == "__main__":
    unittest.main()
