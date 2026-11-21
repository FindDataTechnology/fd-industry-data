"""漏斗文档与脚本输出的一致性（change structure-scout-candidates 3.1）。

**为什么要有这条测试**：`docs/onboarding-funnel.md` 长期写着「candidates 是
unknown，因为只有手写表格」——而代码在 2026-11-10（commit 127a7b5）就接通了。
那是一类特别的假信号：判据看着缺少、实际已可用，读文档的人会得出错误结论。

`doc_counts` 抽数字的规则必须**窄**：宽松正则把 `source-candidates/v1` 的 `1`
和「lit 的 61.5%」的 `61` 都读成计数——两次都是实测出来的错法，都在这里钉住。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import onboarding_funnel as of  # noqa: E402


def _doc(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "funnel.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_reads_pipe_table_numbers(tmp_path):
    p = _doc(tmp_path, """
| 段 | 来源 | 当前值 |
|---|---|---|
| candidates | `data-source-scouting/candidates.json`（`source-candidates/v1`） | 202（accepted 146） |
| landed | `spiders/*/manifest.yaml` 存在 | 165 |
| lit | landed 且有非空 `schedule` | 125 |
""")
    got = of.doc_counts(p)
    assert got == {"candidates": 202, "landed": 165, "lit": 125}, got


def test_percentage_is_not_a_count(tmp_path):
    """「lit 的 61.5%」不许被读成 lit=61。"""
    p = _doc(tmp_path, "| lit | x | 125 |\n\nfresh = lit 的 61.5%\n")
    assert of.doc_counts(p).get("lit") == 125


def test_schema_suffix_is_not_a_count(tmp_path):
    """`source-candidates/v1` 的 1 不许被读成 candidates=1。"""
    p = _doc(tmp_path, "| candidates | 见 source-candidates/v1 | 202 |\n")
    assert of.doc_counts(p).get("candidates") == 202


def test_missing_doc_returns_empty(tmp_path):
    assert of.doc_counts(tmp_path / "nope.md") == {}


def test_check_doc_matches_or_reports(tmp_path):
    p = _doc(tmp_path, "| landed | x | 999 |\n| lit | x | 125 |\n")
    report = {"candidates": {"state": "unknown"}, "landed": 165, "lit": 125}
    rc, notes = of.check_doc(report, p)
    assert rc == 1 and any("landed" in n for n in notes)
    # candidates 在 report 里 state=unknown ⇒ 不参与判（读不到就不判）
    assert not any("candidates" in n and "✗" in n for n in notes)


def test_check_doc_green_when_aligned(tmp_path):
    p = _doc(tmp_path, "| landed | x | 165 |\n| lit | x | 125 |\n")
    rc, _ = of.check_doc({"candidates": {"state": "unknown"}, "landed": 165, "lit": 125}, p)
    assert rc == 0


def test_real_doc_is_current():
    """真文档：三个计数必须与本次输出一致——这就是把 rot 变成可机械检出。"""
    report = {"candidates": of.candidates_stage(), "landed": 165, "lit": 125}
    rc, notes = of.check_doc(report)
    assert rc == 0, notes
