"""装配对账测试：server 面=五工具闭集；pack allowTools ⊆ server 面；env 名单一致。

跨工件不变量（spec R1/R2 + pack R3「提案面只复用」）：
- server.py 的 TOOL_NAMES 与 registry 在册声明、fleet-packs/shared/infra-factory-fleet
  manifest 的 mcpServers.infra-telemetry.allowTools 三方逐字一致；
- 无 mutate 工具名出现在任何一处（红线词 never/forbidden 名单）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FINDATA = REPO.parent
PACK_MANIFEST = FINDATA / "fleet-packs" / "shared" / "infra-factory-fleet" / "manifest.json"

MUTATE_WORDS = re.compile(
    r"(apply|restart|delete|scale|rollout|patch|create|update|deploy|kill|drain)", re.I
)


def _server_tool_names() -> set[str]:
    from infra_telemetry.server import TOOL_NAMES

    return set(TOOL_NAMES)


def _pack_infra_allowtools() -> set[str]:
    manifest = json.loads(PACK_MANIFEST.read_text(encoding="utf-8"))
    for server in manifest["mcpServers"]:
        if server["registryName"] == "infra-telemetry":
            return set(server["allowTools"])
    pytest.fail("pack manifest 缺 infra-telemetry mcpServers 段")


def test_server_surface_is_read_only_closed_set():
    names = _server_tool_names()
    assert names == {
        "gitops_sync",
        "workloads_health",
        "services_liveness",
        "crawl_summary",
        "fleet_cost",
    }
    for n in names:
        assert not MUTATE_WORDS.search(n), f"工具名含 mutate 词，违反 R1: {n}"


def test_pack_allowtools_subset_of_server_surface():
    assert _pack_infra_allowtools() <= _server_tool_names()


def test_registered_tools_match_declaration():
    from infra_telemetry import cost, crawl_summary, gitops, liveness, server, workloads

    collected: set[str] = set()

    class FakeMCP:
        def tool(self, **kw):
            def deco(fn):
                collected.add(kw.get("name", fn.__name__))
                return fn

            return deco

    m = FakeMCP()
    for mod in (gitops, workloads, liveness, crawl_summary, cost):
        mod.register(m)
    assert collected == _server_tool_names()


def test_no_secrets_in_static_sources():
    # 静态面扫凭据形态（真实返回扫描是部署验收项 1.6/1.8，与本互补）
    for path in sorted((REPO / "infra_telemetry").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(
            r"\b(?:ghp|github_pat|sha256)_\w{20,}", text
        ), f"{path.name} 疑似内嵌 token"
        assert not re.search(
            r"postgres(?:ql)?://[^\s'\"]+:[^\s'\"]+@", text
        ), f"{path.name} 疑似内嵌 DSN"
