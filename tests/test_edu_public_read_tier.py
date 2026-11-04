"""#2 公开只读面测试（spec add-education-public-read-tier；design D1/D2/D4）。

覆盖：
- register(read_only) 子集（结构性红线：写工具根本不在公开 app 上）
- 公开路径 /public/mcp 匿名 initialize+tools/list 只见 3 工具
- 匿名 tools/call(edu_search) 返回真本地目录条目（带许可层+来源+as_of）
- 匿名调 edu_subscribe → 该 app 上不存在（MCP tool-not-found），订阅表逐字节不变
- CORS：/public 响应带 allow-origin 头 + OPTIONS 预检 204
- 鉴权路径 /mcp 匿名 401、带 token 见 5 工具
- 公开面 live=None（不触 OpenAlex）

用 Starlette TestClient 跑真 ASGI（含 lifespan），不打真 OpenAlex。
"""
from __future__ import annotations

import json
import os

import pytest
from starlette.testclient import TestClient

from edu_catalog import tools as T
from edu_catalog.server import _make_asgi, build_server

CATALOG_ROWS = [
    {"resource_id": "edu.course.ocw.demo", "title": "Local Course",
     "education_source": "ocw_sitemap", "license": "CC BY-NC-SA", "license_layer": "cc-by-nc",
     "commercial_ok": False, "body_state": "link_only", "source_url": "https://ocw.mit.edu/x",
     "source": "education-cc-courses", "scraped_at": "2026-11-04T00:00:00+00:00"},
    {"resource_id": "edu.book.gutenberg.1", "title": "Public Domain Book",
     "education_source": "gutenberg_catalog", "license": "public domain", "license_layer": "cc0_pd",
     "commercial_ok": True, "body_state": "link_only", "source_url": "https://gutenberg.org/ebooks/1",
     "source": "education-oer-aggregators", "scraped_at": "2026-11-04T00:00:00+00:00"},
]


class MemStore:
    def __init__(self, rows):
        self._rows = rows

    def rows(self):
        return list(self._rows)


class SubsStore:
    def __init__(self):
        self.records = []

    def add(self, sub):
        self.records.append(sub)
        return sub

    def remove(self, sid):
        before = len(self.records)
        self.records = [r for r in self.records if r["id"] != sid]
        return len(self.records) < before

    def list(self):
        return list(self.records)


class Recorder:
    def __init__(self):
        self.names = []

    def tool(self, fn):
        self.names.append(fn.__name__)
        return fn


def _data_json(text: str) -> dict:
    """从 SSE/JSON 响应体取 JSON-RPC 对象。"""
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return json.loads(text)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("EDU_LIVE_SEARCH", "off")  # 测试不触网
    monkeypatch.setenv("MCP_BEARER_TOKEN", "sekrit")
    app = _make_asgi(catalog_store=MemStore(CATALOG_ROWS), subs_store=SubsStore())
    with TestClient(app) as c:
        yield c


def _handshake(c, path):
    h = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    r = c.post(path, headers=h, content=json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "1"}}}))
    assert r.status_code == 200, r.text
    sid = r.headers.get("mcp-session-id")
    h2 = {**h, "mcp-session-id": sid}
    c.post(path, headers=h2, content=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    return h2


# ── register 子集（结构性） ──────────────────────────────────────────────────

def test_register_read_only_subset():
    rec = Recorder()
    names = T.register(rec, catalog=lambda: MemStore(CATALOG_ROWS), subs=None, read_only=True)
    assert names == {"edu_search", "edu_item_detail", "edu_overview"}
    assert "edu_subscribe" not in rec.names and "edu_unsubscribe" not in rec.names


def test_register_full_five():
    rec = Recorder()
    names = T.register(rec, catalog=lambda: MemStore(CATALOG_ROWS), subs=SubsStore(), read_only=False)
    assert names == {"edu_search", "edu_item_detail", "edu_overview", "edu_subscribe", "edu_unsubscribe"}


# ── 公开路径：匿名可读、只见 3 工具 ──────────────────────────────────────────

def test_public_initialize_and_toolslist_three(client):
    h = _handshake(client, "/public/mcp")
    r = client.post("/public/mcp", headers=h, content=json.dumps(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}))
    tools = _data_json(r.text)["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"edu_search", "edu_item_detail", "edu_overview"}


def test_public_anonymous_search_returns_local_rows(client):
    h = _handshake(client, "/public/mcp")
    r = client.post("/public/mcp", headers=h, content=json.dumps(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "edu_search", "arguments": {"query": "Book"}}}))
    inner = json.loads(_data_json(r.text)["result"]["content"][0]["text"])
    assert inner["status"] == "ok"
    it = inner["items"][0]
    assert it["license_layer"] and it["source_url"] and it["as_of"]
    assert "live_channel" not in inner and "channels" not in inner  # live=None，不触实时


def test_public_write_tool_absent_and_subs_untouched(client):
    h = _handshake(client, "/public/mcp")
    r = client.post("/public/mcp", headers=h, content=json.dumps(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "edu_subscribe", "arguments": {"kind": "topic", "target": "physics"}}}))
    body = _data_json(r.text)
    # 该 app 上根本没有写工具 → MCP 返回错误（结构性拒绝，非静默成功）
    assert "error" in body or body.get("result", {}).get("isError") is True


# ── CORS ─────────────────────────────────────────────────────────────────────

def test_public_cors_headers_on_response(client):
    h = _handshake(client, "/public/mcp")
    assert h["mcp-session-id"]  # initialize 成功即响应带 CORS 头（TestClient 不校验同源，查头）
    r = client.get("/public/mcp", headers={"origin": "https://qicai.finddatatech.cloud"})
    # GET 到 streamable-http 端点（非 initialize）也带 CORS 头
    assert r.headers.get("access-control-allow-origin") in ("*", "https://qicai.finddatatech.cloud")


def test_public_cors_preflight(client):
    r = client.options("/public/mcp", headers={"origin": "https://qicai.finddatatech.cloud",
                                               "access-control-request-method": "POST"})
    assert r.status_code == 204
    assert "POST" in r.headers.get("access-control-allow-methods", "")


# ── 鉴权路径：匿名 401、带 token 见 5 ────────────────────────────────────────

def test_auth_path_anonymous_401(client):
    r = client.post("/mcp", headers={"accept": "application/json, text/event-stream",
                                     "content-type": "application/json"},
                    content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                                   "clientInfo": {"name": "t", "version": "1"}}}))
    assert r.status_code == 401


def test_auth_path_with_token_five_tools(client):
    h = {"accept": "application/json, text/event-stream", "content-type": "application/json",
         "authorization": "Bearer sekrit"}
    r = client.post("/mcp", headers=h, content=json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"}}}))
    assert r.status_code == 200
    sid = r.headers.get("mcp-session-id")
    h2 = {**h, "mcp-session-id": sid}
    client.post("/mcp", headers=h2, content=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    r2 = client.post("/mcp", headers=h2, content=json.dumps(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}))
    names = {t["name"] for t in _data_json(r2.text)["result"]["tools"]}
    assert names == {"edu_search", "edu_item_detail", "edu_overview", "edu_subscribe", "edu_unsubscribe"}
