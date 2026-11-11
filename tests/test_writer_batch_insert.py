"""writer 的 crawl_items 批量写：executemany → execute_values（2026-11-11）。

起因与实测见 ``docs/writer-batch-insert-2026-11-11.md``：
``jodi-oil`` 把 ``limit`` 抬到 130000 后，真跑在 DB 里表现为
``idle in transaction`` / ``ClientRead`` 空转 1455s —— 写库阶段按
``executemany`` 的逐行往返跑（17.6 rows/s），11.4 万行要 1.8 小时。

这些用例不连库：批量写法只用 mock 记录调用边界，语义等价性靠 idx 连续性断言。
"""
import json
import os
from unittest import mock

import pytest


def test_insert_items_empty_payload_is_noop():
    from fd_industry_data import writer

    cur = mock.MagicMock()
    assert writer._insert_items(cur, 1, []) == 0
    assert not cur.method_calls


def test_insert_items_uses_execute_values_not_executemany():
    """回归闸：不许退回逐行 executemany（这是本缺陷的原始形态）。"""
    from fd_industry_data import writer

    cur = mock.MagicMock()
    with mock.patch("psycopg2.extras.execute_values") as ev:
        writer._insert_items(cur, 7, [{"a": 1}, {"a": 2}])
    assert ev.call_count == 1
    assert not cur.executemany.called


def test_insert_items_chunks_and_keeps_idx_contiguous():
    """分块必须保住 (run_id, idx) 的全局连续性——idx 是主键的一半。"""
    from fd_industry_data import writer

    payload = [{"i": i} for i in range(2500)]
    seen = []

    def fake_execute_values(cur, sql, rows, page_size=None):
        seen.extend(rows)
        assert page_size == len(rows)

    cur = mock.MagicMock()
    with mock.patch("psycopg2.extras.execute_values", fake_execute_values):
        written = writer._insert_items(cur, 42, payload)

    assert written == 2500
    assert [r[1] for r in seen] == list(range(2500))
    assert {r[0] for r in seen} == {42}
    assert [json.loads(r[2])["i"] for r in seen] == list(range(2500))


def test_insert_items_batch_size_is_bounded():
    """单条语句有上界，避免把 13 万行塞进一个 VALUES。"""
    from fd_industry_data import writer

    assert 100 <= writer._ITEM_BATCH <= 10000
    sizes = []

    def fake_execute_values(cur, sql, rows, page_size=None):
        sizes.append(len(rows))

    payload = [{"i": i} for i in range(writer._ITEM_BATCH * 2 + 5)]
    with mock.patch("psycopg2.extras.execute_values", fake_execute_values):
        writer._insert_items(mock.MagicMock(), 1, payload)

    assert sizes == [writer._ITEM_BATCH, writer._ITEM_BATCH, 5]


def test_writer_source_has_no_executemany_for_items():
    """源码闸：两个写入路径都不该再出现 `cur.executemany(...)` 调用。

    只禁调用，不禁提及——``_insert_items`` 的说明里需要写出"为什么不用它"。
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "fd_industry_data" / "writer.py").read_text(encoding="utf-8")
    assert "cur.executemany" not in src
    assert src.count("_insert_items(cur, run_id, payload)") == 2
