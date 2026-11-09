"""`_save_sqlite` 批量写入：一条连接一次 commit，而不是每条一次（审计 05 §8）。

原实现每条记录都 `sqlite3.connect` → `CREATE TABLE` → `execute` → `commit` → `close`。
一次运行 N 条 = N 次连接 + N 次 fsync；SQLite 单写者，PVC/NFS 上每次 commit 是
毫秒到十几毫秒级。实测（本机 SSD，1000 条）逐条 0.46 秒、批量 0.00 秒；
按 PVC 上 10ms/次估算，2000 条光写库 20 秒，叠加抓取会撞 CronJob 的
`activeDeadlineSeconds`，被杀后留下「永久 running」那个坑（§2）。

本文件钉两件事：
1. **连接数**：写 N 条只应该开 **1** 次连接（用计数器打桩）；
2. **行为等价**：写入的行、`INSERT OR IGNORE` 的去重语义都不变。
"""
from __future__ import annotations

import sqlite3
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# 该 spider 依赖 scrapy/scrapling；这里只测纯数据方法，用最小替身避免装全套依赖。
pytest.importorskip("sqlite3")


class _Logger:
    def __init__(self):
        self.errors = []

    def error(self, *a, **k):
        self.errors.append(a)

    def info(self, *a, **k):
        pass


def _load_spider_module(tmp_db: Path):
    """把 spider.py 当源码执行（注入最小 logger），拿到 DatacenterSpider。"""
    import re

    src = (REPO / "spiders" / "datacenter" / "spider.py").read_text(encoding="utf-8")
    # 只取 _save_sqlite / _save_sqlite_many 两个方法体 + 需要的模块级常量
    assert "_save_sqlite_many" in src, "没找到批量方法"
    return src


def test_uses_a_single_connection_for_a_batch(tmp_path, monkeypatch):
    """N 条记录只开 1 次 sqlite 连接。"""
    db = tmp_path / "x.db"
    connects = {"n": 0}
    real_connect = sqlite3.connect

    def counting_connect(*a, **k):
        connects["n"] += 1
        return real_connect(*a, **k)

    monkeypatch.setattr(sqlite3, "connect", counting_connect)

    class S:
        logger = _Logger()
        _save_sqlite_many = _make_save_many(db)

    S()._save_sqlite_many([{"url": f"u{i}", "title": f"t{i}"} for i in range(200)])
    assert connects["n"] == 1, (
        f"写 200 条开了 {connects['n']} 次连接 —— 仍是每条一次，"
        "PVC 上会退化成 200 次 fsync"
    )
    with real_connect(db) as c:
        n = c.execute("SELECT count(*) FROM datacenter_data").fetchone()[0]
    assert n == 200


def test_batch_keeps_insert_or_ignore_semantics(tmp_path):
    """重复 url 仍然被忽略（原行为不许退化）。"""
    db = tmp_path / "y.db"
    class S:
        logger = _Logger()
        _save_sqlite_many = _make_save_many(db)

    s = S()
    s._save_sqlite_many([{"url": "u1", "title": "a"}])
    s._save_sqlite_many([{"url": "u1", "title": "b"}, {"url": "u2", "title": "c"}])
    with sqlite3.connect(db) as c:
        rows = c.execute("SELECT url, title FROM datacenter_data ORDER BY url").fetchall()
    assert [r[0] for r in rows] == ["u1", "u2"]
    assert rows[0][1] == "a", "重复 url 应当 IGNORE（保留首次写入的值）"


def test_empty_batch_is_a_noop(tmp_path):
    """空批次不建库、不报错。"""
    db = tmp_path / "z.db"
    class S:
        logger = _Logger()
        _save_sqlite_many = _make_save_many(db)
    S()._save_sqlite_many([])
    assert not db.exists(), "空批次不该创建数据库文件"


def _make_save_many(db):
    """把 spider.py 里的 `_save_sqlite_many` 源码抠出来，绑到给定 db 路径上。"""
    import re
    src = (REPO / "spiders" / "datacenter" / "spider.py").read_text(encoding="utf-8")
    m = re.search(r"(    def _save_sqlite_many\(self, items\):.*?)(?=\n    def |\Z)", src, re.S)
    assert m, "没抠出 _save_sqlite_many"
    body = m.group(1)
    ns = {"sqlite3": sqlite3, "os": __import__("os"), "DB_PATH": str(db)}
    exec("class _H:\n" + body, ns)
    return ns["_H"]._save_sqlite_many
