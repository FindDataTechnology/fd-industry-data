"""Accept-conditions evaluated at commit time.

The disease: a source runs green every day and writes zero rows, so the board is
empty and nothing is red. The inspector already had a rule for this
(`min_rows_per_run` → ticket), but it judges *after* the run and can only write the
verdict down. Evaluating before the status is committed is what makes the source's
own promise binding.

**This reader deliberately reuses `min_rows_per_run`** rather than introducing a
second spelling of the same promise — an earlier version invented `accept.min_rows`
and that duplication is the exact defect this workspace keeps rediscovering.

The property that makes it safe to ship on 160 sources: no declaration → identical
behaviour.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from fd_industry_data.health.inspector import min_rows_floor
from fd_industry_data.runner_cli import _accept_block, _evaluate_accept, _min_rows_per_run


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="accept-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._old = os.environ.get("FD_CONTENT_DIR")
        os.environ["FD_CONTENT_DIR"] = str(self.tmp)

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("FD_CONTENT_DIR", None)
        else:
            os.environ["FD_CONTENT_DIR"] = self._old

    def source(self, body: str) -> str:
        """Write a manifest where BOTH readers look for it.

        `runner_cli` searches `<FD_CONTENT_DIR>/<source>` while
        `health.inspector.min_rows_floor` searches `<repo>/spiders/<source>`. The
        fixture nests under `spiders/` and points FD_CONTENT_DIR at it, so the two
        paths coincide — which is also how the real layout works.
        """
        root = self.tmp / "spiders"
        d = root / "demo"
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.yaml").write_text(body, encoding="utf-8")
        os.environ["FD_CONTENT_DIR"] = str(root)
        self.repo = self.tmp
        return "demo"


class NotDeclaredTests(Base):
    def test_no_declaration_yields_none(self) -> None:
        """None means "not declared" — the caller must then write no metrics key."""
        name = self.source('version: "2"\nname: demo\nschedule: "0 8 * * *"\n')
        self.assertIsNone(_min_rows_per_run(name))
        self.assertIsNone(_accept_block(name))
        self.assertIsNone(_evaluate_accept(name, status="success", rows=0, env=os.environ))

    def test_missing_manifest_yields_none(self) -> None:
        self.assertIsNone(_evaluate_accept("no-such-source", status="success", rows=0, env=os.environ))

    def test_empty_manifest_yields_none(self) -> None:
        self.assertIsNone(_evaluate_accept(self.source(""), status="success", rows=0, env=os.environ))


class ReuseTests(Base):
    """The two readers must agree, or one of them is lying to somebody."""

    def test_same_value_as_the_inspector(self) -> None:
        for body, expected in [
            ('min_rows_per_run: 10\n', 10),
            ('min_rows_per_run: 1\n', 1),
            ('min_rows_per_run: 0\n', None),
            ('min_rows_per_run: -5\n', None),
            # A quoted value is a YAML string; the inspector rejects it, so this
            # reader must too or the two would disagree about one manifest.
            ('min_rows_per_run: "10"\n', None),
            ('# min_rows_per_run: 10\n', None),      # commented out
            ('min_rows_per_run: abc\n', None),
            ('', None),
        ]:
            name = self.source(body)
            self.assertEqual(_min_rows_per_run(name), expected, body)
            # Compare with the inspector's reader only where it can actually run:
            # `min_rows_floor` imports yaml, which is absent in some environments
            # (measured on the dev box). A skipped comparison here is honest; a
            # fabricated one would hide the day the two really do diverge.
            try:
                inspector = min_rows_floor(self.repo, name)
            except ModuleNotFoundError:
                continue
            self.assertEqual(
                _min_rows_per_run(name), inspector, f"readers disagree on {body!r}"
            )

    def test_inline_comment_is_ignored(self) -> None:
        name = self.source("min_rows_per_run: 7   # set after the 2026-11 outage\n")
        self.assertEqual(_min_rows_per_run(name), 7)


class MinRowsTests(Base):
    def test_zero_rows_against_a_floor_is_not_ok(self) -> None:
        """The exact failure: run green, zero rows, board empty."""
        name = self.source("name: demo\nmin_rows_per_run: 10\n")
        result = _evaluate_accept(name, status="success", rows=0, env=os.environ)
        self.assertFalse(result["ok"])
        self.assertEqual(result["checks"][0]["actual"], 0)
        self.assertEqual(result["checks"][0]["expected"], 10)

    def test_enough_rows_is_ok(self) -> None:
        name = self.source("name: demo\nmin_rows_per_run: 10\n")
        self.assertTrue(_evaluate_accept(name, status="success", rows=10, env=os.environ)["ok"])

    def test_boundary_is_inclusive(self) -> None:
        name = self.source("name: demo\nmin_rows_per_run: 3\n")
        self.assertTrue(_evaluate_accept(name, status="success", rows=3, env=os.environ)["ok"])


class UnjudgedTests(Base):
    def test_max_age_is_recorded_but_not_judged(self) -> None:
        """The runner cannot know the payload's age; claiming a pass would be a lie."""
        name = self.source("name: demo\naccept:\n  max_age_days: 45\n")
        result = _evaluate_accept(name, status="success", rows=100, env=os.environ)
        age = next(c for c in result["checks"] if c["name"] == "max_age_days")
        self.assertIsNone(age["ok"], "unjudged must not be True")

    def test_unjudged_alone_does_not_fail_the_run(self) -> None:
        name = self.source("name: demo\naccept:\n  max_age_days: 45\n")
        self.assertTrue(_evaluate_accept(name, status="success", rows=1, env=os.environ)["ok"])

    def test_unknown_key_in_accept_fails_loudly(self) -> None:
        """A typo would otherwise accept everything, which is the opposite of a rule."""
        name = self.source("name: demo\naccept:\n  min_row: 10\n")
        result = _evaluate_accept(name, status="success", rows=100, env=os.environ)
        self.assertFalse(result["ok"])
        unknown = next(c for c in result["checks"] if c["name"] == "unknown-keys")
        self.assertEqual(unknown["actual"], ["min_row"])


class NeverBreaksACrawlTests(Base):
    def test_any_input_returns_or_none_without_raising(self) -> None:
        for source in ("", "demo", "../etc/passwd", "a/b", ".hidden", "nonexistent"):
            try:
                _evaluate_accept(source, status="success", rows=0, env=os.environ)
            except Exception as exc:  # noqa: BLE001
                self.fail(f"raised for {source!r}: {exc}")

    def test_comments_do_not_break_the_block(self) -> None:
        name = self.source(
            "name: demo\n"
            "accept:\n"
            "  # unverifiable by the runner\n"
            "  max_age_days: 45   # trailing comment\n"
        )
        result = _evaluate_accept(name, status="success", rows=5, env=os.environ)
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()


class PartialIsActuallyWritten(unittest.TestCase):
    """端到端：manifest 声明下限、蜘蛛写不够时，运行记录的状态必须是 `partial`。

    这里补的是本文件长期缺的一环。上面所有用例都只测 `_evaluate_accept` / `_accept_block`
    这些纯函数——**没有一个验证 `main()` 真把 `partial` 写进运行记录**。
    而"runner 已经在写 partial"这句话，我在这轮的所有判断里都当作既成事实用了：
    巡检侧不认识 partial 会误判、证据里把 partial 当失败……
    如果这个状态其实没被写出来，那一整串分析都建在假设上。

    照 `test_attribution` 的做法：替换写库函数，跑真实的 `main()`。
    """

    def test_declared_floor_unmet_is_recorded_as_partial(self) -> None:
        import os
        import tempfile
        from pathlib import Path

        from fd_industry_data import runner_cli

        tmp = Path(tempfile.mkdtemp(prefix="partial-e2e-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, True))
        src = tmp / "spiders" / "short-demo"
        src.mkdir(parents=True)
        (src / "manifest.yaml").write_text(
            'version: "2"\nname: short-demo\nlabel: demo\n'
            'source_url: https://example.gov.cn/stats/\n'
            'data_period: day\nmin_rows_per_run: 5\n'
            'functions:\n  - command: run_short_demo\n'
            '    columns:\n      - {name: v, type: str, description: value}\n',
            encoding="utf-8",
        )
        # 只写 2 行，低于声明的 5
        (src / "spider.py").write_text(
            "def run_short_demo(limit: int = 10):\n"
            "    return [{'v': str(i)} for i in range(2)]\n",
            encoding="utf-8",
        )

        captured: dict = {}
        saved = {n: getattr(runner_cli, n) for n in
                 ("open_run_for", "start_run", "finish_run", "cancel_requested")}
        old_env = os.environ.get("FD_CONTENT_DIR")
        old_cwd = os.getcwd()
        try:
            runner_cli.open_run_for = lambda s: None
            runner_cli.start_run = lambda **kw: 999
            runner_cli.finish_run = lambda run_id, **kw: (captured.update(kw), True)[1]
            runner_cli.cancel_requested = lambda run_id: False
            os.environ["FD_CONTENT_DIR"] = str(tmp / "spiders")
            os.chdir(tmp)
            rc = runner_cli.main(["short-demo"])
        finally:
            for name, fn in saved.items():
                setattr(runner_cli, name, fn)
            if old_env is None:
                os.environ.pop("FD_CONTENT_DIR", None)
            else:
                os.environ["FD_CONTENT_DIR"] = old_env
            os.chdir(old_cwd)

        self.assertEqual(rc, 0)
        self.assertEqual(captured.get("status"), "partial",
                         "低于声明下限的运行必须记 partial，不能记 success")
        self.assertEqual(captured.get("rows_written"), 2)
        accept = (captured.get("metrics") or {}).get("accept")
        self.assertIsNotNone(accept, "accept 结论必须随运行入库，供巡检与工单证据用")
        self.assertFalse(accept["ok"])

    def test_undeclared_source_still_records_success(self) -> None:
        """没有声明 = 行为完全不变（零回归）。"""
        import os
        import tempfile
        from pathlib import Path

        from fd_industry_data import runner_cli

        tmp = Path(tempfile.mkdtemp(prefix="partial-none-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, True))
        src = tmp / "spiders" / "plain-demo"
        src.mkdir(parents=True)
        (src / "manifest.yaml").write_text(
            'version: "2"\nname: plain-demo\nlabel: demo\n'
            'source_url: https://example.gov.cn/stats/\ndata_period: day\n'
            'functions:\n  - command: run_plain_demo\n'
            '    columns:\n      - {name: v, type: str, description: value}\n',
            encoding="utf-8",
        )
        (src / "spider.py").write_text(
            "def run_plain_demo(limit: int = 10):\n"
            "    return [{'v': 'x'}]\n", encoding="utf-8")

        captured: dict = {}
        saved = {n: getattr(runner_cli, n) for n in
                 ("open_run_for", "start_run", "finish_run", "cancel_requested")}
        old_env = os.environ.get("FD_CONTENT_DIR")
        old_cwd = os.getcwd()
        try:
            runner_cli.open_run_for = lambda s: None
            runner_cli.start_run = lambda **kw: 999
            runner_cli.finish_run = lambda run_id, **kw: (captured.update(kw), True)[1]
            runner_cli.cancel_requested = lambda run_id: False
            os.environ["FD_CONTENT_DIR"] = str(tmp / "spiders")
            os.chdir(tmp)
            rc = runner_cli.main(["plain-demo"])
        finally:
            for name, fn in saved.items():
                setattr(runner_cli, name, fn)
            if old_env is None:
                os.environ.pop("FD_CONTENT_DIR", None)
            else:
                os.environ["FD_CONTENT_DIR"] = old_env
            os.chdir(old_cwd)

        self.assertEqual(rc, 0)
        self.assertEqual(captured.get("status"), "success")
        self.assertIsNone((captured.get("metrics") or {}).get("accept"))
