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
