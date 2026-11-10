"""恢复脚本：暂撤的声明要能被一条命令还原，且不满足前置条件时拒绝。

背景：2026-11-10 因 11-06 批量探针误报，暂撤了 boxofficemojo/cisa/gb-standards 三个声明。
原计划"恢复动作留给人"——那种活没人会记得，忘记的后果是三个源永久失去下限守卫（静默降级）。
本脚本让恢复可干跑、幂等、并且**源还没重跑时拒绝恢复**（否则会立刻重新误报）。
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "restore_min_rows.py"


def load():
    spec = importlib.util.spec_from_file_location("restore_min_rows", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    sys.modules["restore_min_rows"] = m
    assert spec.loader is not None
    spec.loader.exec_module(m)
    return m


WITHDRAWN = """name: {src}
data_period: month
# min_rows_per_run 暂撤（2026-11-10）：最近一次成功 rows=20 来自 11-06 的批量探针
#   （48 源各一次、恰好 20 行、image_tag=unknown，非 k8s 调度），不是源低产。
#   下一次真实运行 2026-11-12（周四 02:25）后恢复——届时最新可比成功才有意义。
scanner_mode: upstream-curated
"""


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.tmp = Path(tempfile.mkdtemp(prefix="restore-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "spiders").mkdir()
        self.m.SPIDERS = self.tmp / "spiders"
        self.m.RESTORE = {"demo": "min_rows_per_run: 25    # runs=6 p10=60，取 25"}
        self.m.WITHDRAWN_AT = "2026-11-10T06:22:44+00:00"

    def manifest(self, body: str) -> Path:
        d = self.tmp / "spiders" / "demo"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "manifest.yaml"
        p.write_text(body, encoding="utf-8")
        return p

    def test_refuses_when_source_has_not_rerun(self):
        self.manifest(WITHDRAWN.format(src="demo"))
        records = [{"source": "demo", "status": "success", "rows": 20,
                    "started_at": "2026-11-06T09:54:00+00:00"}]
        edits, notes = self.m.plan(records, require_fresh=True)
        self.assertEqual(edits, [], "源没重跑就恢复，会立刻重新误报")
        self.assertTrue(any("尚未重跑" in n for n in notes), notes)

    def test_restores_after_a_fresh_run(self):
        p = self.manifest(WITHDRAWN.format(src="demo"))
        records = [{"source": "demo", "status": "success", "rows": 100,
                    "started_at": "2026-11-12T02:30:00+00:00"}]
        edits, notes = self.m.plan(records, require_fresh=True)
        self.assertEqual(len(edits), 1, notes)
        written, skipped = self.m.apply_edits(edits)
        self.assertEqual(written, [p])
        body = p.read_text(encoding="utf-8")
        self.assertIn("min_rows_per_run: 25", body)
        self.assertNotIn("暂撤", body, "注释必须一并删掉")
        # 插回 data_period 之后，保持与 declare 脚本同一形状
        lines = body.splitlines()
        idx = next(i for i, l in enumerate(lines) if l.startswith("data_period:"))
        self.assertTrue(lines[idx + 1].startswith("min_rows_per_run"))

    def test_run_later_on_the_withdrawal_day_counts_as_rerun(self):
        """撤除当天稍晚的运行必须被认可。

        原先判据是 `at[:10] <= "2026-11-10"`（按**日期**），会把 11-10 全天的运行
        都排除——若当天有人手动跑一次验证，那是撤除**之后**的有效运行，恢复却被
        无故拒绝。这类阻塞只在"当天跑过"时才现形，很难被发现。
        """
        self.manifest(WITHDRAWN.format(src="demo"))
        records = [{"source": "demo", "status": "success", "rows": 100,
                    "started_at": "2026-11-10T15:30:00+00:00"}]   # 撤除之后、当天
        edits, notes = self.m.plan(records, require_fresh=True)
        self.assertEqual(len(edits), 1, notes)

    def test_run_before_the_withdrawal_moment_does_not_count(self):
        """撤除**之前**的运行不算重跑——那时"最新可比成功"仍是那条探针。"""
        self.manifest(WITHDRAWN.format(src="demo"))
        records = [{"source": "demo", "status": "success", "rows": 100,
                    "started_at": "2026-11-10T02:00:00+00:00"}]
        edits, notes = self.m.plan(records, require_fresh=True)
        self.assertEqual(edits, [], notes)

    def test_idempotent_when_already_declared(self):
        self.manifest("name: demo\ndata_period: month\nmin_rows_per_run: 25\n")
        edits, notes = self.m.plan([], require_fresh=False)
        self.assertEqual(edits, [])
        self.assertTrue(any("已声明" in n for n in notes), notes)

    def test_refuses_when_withdrawal_note_is_missing(self):
        """没人能保证 manifest 没被人改过；找不到暂撤注释就不动。"""
        self.manifest("name: demo\ndata_period: month\n")
        edits, notes = self.m.plan([], require_fresh=False)
        self.assertEqual(edits, [])
        self.assertTrue(any("找不到暂撤注释" in n for n in notes), notes)

    def test_concurrent_edit_is_not_overwritten(self):
        p = self.manifest(WITHDRAWN.format(src="demo"))
        edits, _ = self.m.plan([], require_fresh=False)
        p.write_text("name: demo\ndata_period: month\n# 别人改过\n", encoding="utf-8")
        written, skipped = self.m.apply_edits(edits)
        self.assertEqual(written, [])
        self.assertEqual(skipped, [p])


if __name__ == "__main__":
    unittest.main()
