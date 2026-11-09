"""声明脚本本身要被钉住：它写的是 160 份生产 manifest 里的 20 份。

三条关键性质：
1. **幂等**：已声明的源跳过，不插第二遍
2. **两侧一致**：写进去的值必须被 runner 与 inspector 读出同一个数
3. **错误路径不炸**：某个源缺失时报一句，不能抛异常把整批带崩
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
SCRIPT = PKG / "scripts" / "declare_min_rows.py"


def load_script():
    spec = importlib.util.spec_from_file_location("declare_min_rows", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DeclareMinRowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = load_script()
        self.tmp = Path(tempfile.mkdtemp(prefix="declare-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "spiders").mkdir()
        self.mod.SPIDERS = self.tmp / "spiders"

    def manifest(self, name: str, body: str) -> Path:
        d = self.tmp / "spiders" / name
        d.mkdir(parents=True, exist_ok=True)
        p = d / "manifest.yaml"
        p.write_text(body, encoding="utf-8")
        return p

    def test_inserts_after_data_period_with_rationale(self) -> None:
        self.manifest("bls", 'name: bls\nschedule: "0 8 * * *"\ndata_period: month\n')
        edits, _ = self.mod.plan()
        self.assertEqual(len(edits), 1)
        path, _original, text = edits[0]
        lines = text.splitlines()
        field = [ln for ln in lines if ln.startswith("min_rows_per_run:")]
        self.assertEqual(len(field), 1)
        self.assertIn("min_rows_per_run: 1", field[0])
        self.assertIn("p10=1", field[0], "the rationale must be on the line, per task 2.1")
        # 紧跟在 data_period 之后
        idx = lines.index("data_period: month")
        self.assertTrue(lines[idx + 1].startswith("min_rows_per_run"))

    def test_is_idempotent(self) -> None:
        p = self.manifest("bls", "name: bls\ndata_period: month\n")
        edits, _ = self.mod.plan()
        p.write_text(edits[0][2], encoding="utf-8")
        edits2, problems = self.mod.plan()
        self.assertEqual(edits2, [], "a declared source must not be edited again")
        self.assertTrue(any("已声明" in x for x in problems))

    def test_missing_source_reports_without_raising(self) -> None:
        """One absent source must not turn a partial run into a traceback."""
        edits, problems = self.mod.plan()
        self.assertEqual(edits, [])
        self.assertTrue(any("manifest 不存在" in x for x in problems))

    def test_no_anchor_reports_without_raising(self) -> None:
        self.manifest("bls", "version: \"2\"\n")
        edits, problems = self.mod.plan()
        self.assertEqual(edits, [])
        self.assertTrue(any("找不到插入锚点" in x for x in problems))

    def test_declared_values_match_the_baseline_table(self) -> None:
        """Every value in the script must be traceable to the documented p10/p50."""
        for source, (runs, p10, p50, value, why) in self.mod.DECLARATIONS.items():
            self.assertGreater(runs, 0, source)
            self.assertGreater(value, 0, source)
            self.assertTrue(why.strip(), f"{source}: every value needs a stated reason")
            if p50 == 100:
                # p50 sitting exactly at the default cap means every page is truncated:
                # the threshold must be a fraction of the cap, never the cap itself
                # (that would fail every short page).
                self.assertLess(value, 100, f"{source}: p50 at the cap means a fraction, not 100")
            else:
                # Not cap-truncated: the floor must stay under the observed p10, or the
                # source would fail on its own normal low-volume run.
                self.assertLessEqual(value, p10, f"{source}: must not exceed observed p10")


    def test_skipped_sources_are_disjoint_and_explained(self) -> None:
        """A bucket source we deliberately do not declare must say why, once.

        Without this, the next reader cannot tell "forgot" from "decided not to".
        """
        self.assertFalse(set(self.mod.DECLARATIONS) & set(self.mod.SKIPPED))
        for source, why in self.mod.SKIPPED.items():
            self.assertTrue(why.strip(), f"{source}: a skip needs a stated reason")

    def test_concurrent_edit_is_not_overwritten(self) -> None:
        """A file changed between plan() and apply must be skipped, not clobbered.

        This tree is shared with other sessions; the archived
        `define-ingest-revision-and-coverage` batch landed in these same files. Losing
        one of those appends would undo a ratified change with no error message.
        """
        p = self.manifest("bls", "name: bls\ndata_period: month\n")
        edits, _ = self.mod.plan()
        other_session = "name: bls\ndata_period: month\nrevision_policy: overwrite_with_history\n"
        p.write_text(other_session, encoding="utf-8")

        written, skipped = self.mod.apply_edits(edits)
        self.assertEqual(written, [], "must not write over a file edited since plan()")
        self.assertEqual(skipped, [p])
        self.assertEqual(p.read_text(encoding="utf-8"), other_session)

    def test_unchanged_file_is_written(self) -> None:
        p = self.manifest("bls", "name: bls\ndata_period: month\n")
        edits, _ = self.mod.plan()
        written, skipped = self.mod.apply_edits(edits)
        self.assertEqual(written, [p])
        self.assertEqual(skipped, [])
        self.assertIn("min_rows_per_run: 1", p.read_text(encoding="utf-8"))


class BothReadersAgreeTests(unittest.TestCase):
    """The inserted field must be read identically by runner and inspector."""

    def setUp(self) -> None:
        try:
            import yaml  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("yaml is required to exercise the inspector's reader")
        self.mod = load_script()
        self.tmp = Path(tempfile.mkdtemp(prefix="declare-agree-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "spiders").mkdir()
        self.mod.SPIDERS = self.tmp / "spiders"
        self._old = os.environ.get("FD_CONTENT_DIR")
        os.environ["FD_CONTENT_DIR"] = str(self.tmp / "spiders")

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("FD_CONTENT_DIR", None)
        else:
            os.environ["FD_CONTENT_DIR"] = self._old

    def test_inserted_values_agree(self) -> None:
        from fd_industry_data.health.inspector import min_rows_floor
        from fd_industry_data.runner_cli import _min_rows_per_run

        for source in ("bls", "hkex-disclosure", "kitco"):
            d = self.tmp / "spiders" / source
            d.mkdir(parents=True, exist_ok=True)
            (d / "manifest.yaml").write_text(
                f"name: {source}\ndata_period: month\n", encoding="utf-8"
            )
        edits, _ = self.mod.plan()
        for path, _original, text in edits:
            path.write_text(text, encoding="utf-8")
        for source in ("bls", "hkex-disclosure", "kitco"):
            runner = _min_rows_per_run(source)
            inspector = min_rows_floor(self.tmp, source)
            self.assertIsNotNone(runner, source)
            self.assertEqual(runner, inspector, f"readers disagree on {source}")


if __name__ == "__main__":
    unittest.main()
