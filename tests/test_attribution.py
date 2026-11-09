"""每次运行都该记录"谁跑的"和"读的哪个上游"。

这两个事实以前都没记，导致下游只能看到"某次运行写了 133 行"，既无法归因，
也不知道是哪个上游挂了。它们进已有的 `metrics` jsonb，不动表结构。

最重要的一条约束：**归因失败绝不能让采集失败**。所以每个用例都要求不抛。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from fd_industry_data.runner_cli import _attribution


class AttributionTests(unittest.TestCase):
    def test_owner_comes_from_hostname(self) -> None:
        got = _attribution("moa-hog-price", {"HOSTNAME": "crawler-abc123"})
        self.assertEqual(got["owner"], "crawler-abc123")

    def test_owner_falls_back_through_the_order(self) -> None:
        self.assertEqual(_attribution("x", {"FD_RUN_OWNER": "job-7"})["owner"], "job-7")
        self.assertEqual(_attribution("x", {"COMPUTERNAME": "WINBOX"})["owner"], "WINBOX")

    def test_missing_env_falls_back_to_the_real_hostname(self) -> None:
        """SEMANTICS CHANGED: no env var is no longer "unknown".

        `HOSTNAME` is only a shell variable on macOS zsh — it is not exported — so
        every run on the dev box recorded "unknown" while a real hostname was
        available. `socket.gethostname()` is the fallback. "unknown" now means the
        hostname could not be obtained either.
        """
        got = _attribution("x", {})
        self.assertTrue(got["owner"])
        self.assertNotEqual(got["owner"], "")

    def test_upstream_read_from_manifest(self) -> None:
        got = _attribution("moa-hog-price", {})
        self.assertIsInstance(got["upstreams"], list)
        self.assertTrue(got["upstreams"][0].startswith("http"))

    def test_unknown_source_yields_unknown_upstream(self) -> None:
        got = _attribution("no-such-source-xyz", {})
        self.assertEqual(got["upstreams"], "unknown")

    def test_never_raises_for_any_input(self) -> None:
        for source, env in [
            ("", {}), ("../etc/passwd", {}), ("moa-hog-price", {"HOSTNAME": ""}),
            ("x", {"HOSTNAME": "   "}),
        ]:
            try:
                got = _attribution(source, env)
            except Exception as exc:  # noqa: BLE001
                self.fail(f"_attribution raised for {source!r}/{env!r}: {exc}")
            self.assertIn("owner", got)
            self.assertIn("upstreams", got)

    def test_whitespace_only_env_does_not_become_a_blank_owner(self) -> None:
        """Blank must not leak through: it reads downstream as "nobody"."""
        owner = _attribution("x", {"HOSTNAME": "   "})["owner"]
        self.assertNotEqual(owner.strip(), "")


class EndToEndTests(unittest.TestCase):
    """Run the REAL runner path, stubbing only the DB write.

    Unit tests on `_attribution` all passed while the feature was broken: the
    hostname came from an env var macOS does not export, and the manifest was
    looked up in the package directory instead of `FD_CONTENT_DIR`. Both were only
    visible by running `runner_cli.main()`. That is why this class exists.
    """

    def test_owner_and_upstreams_reach_metrics_through_the_runner(self) -> None:
        import json
        import os
        import tempfile
        from pathlib import Path

        from fd_industry_data import runner_cli

        tmp = Path(tempfile.mkdtemp(prefix="attr-e2e-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, True))
        src_dir = tmp / "spiders" / "attr-demo"
        src_dir.mkdir(parents=True)
        (src_dir / "manifest.yaml").write_text(
            'version: "2"\nname: attr-demo\nlabel: demo\n'
            'source_url: https://example.gov.cn/stats/\n'
            'data_period: month\nfunctions:\n  - command: run_attr_demo\n'
            '    columns:\n      - {name: v, type: str, description: value}\n',
            encoding="utf-8",
        )
        (src_dir / "spider.py").write_text(
            "def run_attr_demo(limit: int = 10):\n"
            "    return [{'v': str(i)} for i in range(limit)]\n",
            encoding="utf-8",
        )

        captured: dict = {}
        saved = {
            "open_run_for": runner_cli.open_run_for,
            "start_run": runner_cli.start_run,
            "finish_run": runner_cli.finish_run,
            "cancel_requested": runner_cli.cancel_requested,
        }
        old_env = os.environ.get("FD_CONTENT_DIR")
        old_cwd = os.getcwd()
        try:
            runner_cli.open_run_for = lambda src: None
            runner_cli.start_run = lambda **kw: 999
            runner_cli.finish_run = lambda run_id, **kw: (captured.update(kw), True)[1]
            runner_cli.cancel_requested = lambda run_id: False
            os.environ["FD_CONTENT_DIR"] = str(tmp / "spiders")
            os.chdir(tmp)
            rc = runner_cli.main(["attr-demo", "--limit", "2"])
        finally:
            for name, fn in saved.items():
                setattr(runner_cli, name, fn)
            if old_env is None:
                os.environ.pop("FD_CONTENT_DIR", None)
            else:
                os.environ["FD_CONTENT_DIR"] = old_env
            os.chdir(old_cwd)

        self.assertEqual(rc, 0)
        metrics = captured.get("metrics") or {}
        self.assertTrue(metrics.get("owner"), "every run records a holder")
        self.assertNotEqual(metrics["owner"], "unknown",
                            "the hostname fallback must produce a real value")
        self.assertEqual(metrics.get("upstreams"), ["https://example.gov.cn/stats/"],
                         "the manifest is found through FD_CONTENT_DIR, not the package dir")
        # The effective limit must travel with the run: `rows_written=20` is only
        # interpretable if you know whether 20 was the answer or the ceiling.
        # Measured 2026-11-06: 48 sources each ran once with a small limit and all
        # wrote exactly 20 — indistinguishable from a data collapse without this.
        self.assertEqual(metrics.get("limit"), 2,
                         "the run must record what it was allowed to return")


if __name__ == "__main__":
    unittest.main()
