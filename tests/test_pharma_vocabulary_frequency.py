"""医药词表的频率拼写守卫（change fix-concept-freshness-window 的回归门）。

背景：`fd-open-data-mcp` 的新鲜度表 `fetch/cache.py:_TTL` 只认固定几个键
（`realtime/daily/irregular/weekly/monthly/quarterly/yearly/unknown`），未命中即默认
**1 小时**窗口。词表里写 `annual` / `month` 不会报错，只会让年度概念抓完一小时就被判过期，
点读退化为 `no eligible source`（实测：目录两概念）。

裸写 YAML 断言，不连库、不 import 内核——这样 CI 里跑得动，而问题在词表里就能被拦住。
若内核将来扩充 `_TTL` 键，这里要同步加，这是**有意保留的耦合**：两个词表必须对得上。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
VOCAB = (
    REPO.parent
    / "fd-open-data-protocol"
    / "fd_open_data_protocol"
    / "vocabulary"
    / "concepts.yaml"
)

# 与 fd-open-data-mcp/fd_open_data_mcp/fetch/cache.py:_TTL 的键逐一对应
CANONICAL_FREQUENCIES = frozenset({
    "realtime", "daily", "irregular", "weekly",
    "monthly", "quarterly", "yearly", "unknown",
})

PHARMA_FAMILIES = ("ClinicalTrial", "InsuranceCatalogEntry")


def _pharma_variables() -> list[dict]:
    if not VOCAB.exists():  # 单仓 checkout（没有兄弟仓）时跳过，而不是假绿
        pytest.skip(f"词表不在同级仓：{VOCAB}")
    doc = yaml.safe_load(VOCAB.read_text(encoding="utf-8"))
    out = []
    for entry in doc["entries"]:
        if entry.get("id") in PHARMA_FAMILIES:
            out.extend(entry.get("variables") or [])
    assert out, f"{PHARMA_FAMILIES} 下没有任何变量种子"
    return out


def test_pharma_frequencies_are_canonical():
    bad = [(v["code"], v["frequency"]) for v in _pharma_variables()
           if v["frequency"].lower() not in CANONICAL_FREQUENCIES]
    assert not bad, (
        f"非规范频率会让该概念只用 1 小时新鲜期（实测症状：点读 no eligible source）：{bad}。"
        f"允许值：{sorted(CANONICAL_FREQUENCIES)}"
    )


def test_pharma_variables_declare_unit():
    missing = [v["code"] for v in _pharma_variables() if not v.get("unit")]
    assert not missing, f"观测单位必须声明（落库 unit 由它决定）：{missing}"
