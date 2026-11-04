"""目录存储 v1（JSONL）+ 订阅记录存储——查询面的数据底。

权威库上产后（deploy 段）换 Postgres 只读视图，接口不变。
订阅记录与目录行物理分离（spec cap2 R1「写面边界可证」的实现前提）。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path


class CatalogStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        # 按 \n 切而不用 splitlines()：后者会在 \u2028/\u2029 等 Unicode 行分隔符
        # 处断行（EPMC 摘要实测含），而 json.dumps 保证字符串内无裸 \n——
        # 2026-11-04 试点批次撞出此 bug 的定点修复。
        for line in self.path.read_text().split("\n"):
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def bytes_snapshot(self) -> bytes:
        return self.path.read_bytes() if self.path.exists() else b""


class SubscriptionStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text() or "[]")

    def _save(self, subs: list[dict]) -> None:
        self.path.write_text(json.dumps(subs, ensure_ascii=False, indent=1))

    def add(self, sub: dict) -> dict:
        subs = self._load()
        subs.append(sub)
        self._save(subs)
        return sub

    def remove(self, subscription_id: str) -> bool:
        subs = self._load()
        kept = [s for s in subs if s.get("id") != subscription_id]
        if len(kept) == len(subs):
            return False
        self._save(kept)
        return True

    def list(self) -> list[dict]:
        return self._load()
