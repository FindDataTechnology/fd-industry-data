"""requires_env preflight tests (harden-crawl-delivery 2.5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from fd_industry_data.runner_cli import EXIT_BLOCKED_CONFIG, missing_required_env


def _unit(root: Path, name: str, manifest: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(manifest)
    return d


def test_missing_declaration_reports_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    _unit(tmp_path, "src-a", 'name: src-a\nschedule: "0 4 * * *"\n')
    assert missing_required_env("src-a", str(tmp_path)) == []


def test_all_present_reports_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", "x")
    _unit(tmp_path, "src-a", 'name: src-a\nrequires_env: ["CENSUS_API_KEY"]\n')
    assert missing_required_env("src-a", str(tmp_path)) == []


def test_missing_env_is_named(tmp_path, monkeypatch):
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    _unit(tmp_path, "src-a",
          'name: src-a\nrequires_env: ["CENSUS_API_KEY", "EIA_API_KEY"]\n')
    assert missing_required_env("src-a", str(tmp_path)) == ["CENSUS_API_KEY", "EIA_API_KEY"]


def test_partial_presence_names_only_the_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", "x")
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    _unit(tmp_path, "src-a",
          'name: src-a\nrequires_env: ["CENSUS_API_KEY", "EIA_API_KEY"]\n')
    assert missing_required_env("src-a", str(tmp_path)) == ["EIA_API_KEY"]


def test_absent_manifest_and_malformed_declaration_are_tolerated(tmp_path):
    assert missing_required_env("nope", str(tmp_path)) == []
    _unit(tmp_path, "src-b", "name: src-b\nrequires_env: CENSUS_API_KEY\n")
    assert missing_required_env("src-b", str(tmp_path)) == []


def test_runner_exits_blocked_config_without_fetching(tmp_path, monkeypatch):
    """The whole point: no fetch, a distinct exit code, and (dry-run) no DB write."""
    from fd_industry_data import runner_cli

    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    monkeypatch.setenv("FD_CONTENT_DIR", str(tmp_path))
    _unit(tmp_path, "src-c",
          'name: src-c\nrequires_env: ["CENSUS_API_KEY"]\n')
    (tmp_path / "src-c" / "spider.py").write_text(
        "def run_src_c(limit=100):\n    raise AssertionError('must not be fetched')\n")

    rc = runner_cli.main(["src-c", "--dry-run"])
    assert rc == EXIT_BLOCKED_CONFIG
