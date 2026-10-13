"""声明命令可达性规则测试（conformance_gate `declared-command-reachability`）。

契约：runner 恒定加载 run_<slug>，没有第二个入口。故声明的 command 必须 (a) 就是
那个入口，(b) 在入口的调用闭包内，或 (c) 在 provider.py 的分派表里——否则它就是死
声明，无论有没有 golden（golden 只证明函数能跑，不证明生产会调用它）。
"""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "conformance_gate", REPO / "scripts" / "conformance_gate.py")
gate = importlib.util.module_from_spec(_spec)
sys.modules["conformance_gate"] = gate
_spec.loader.exec_module(gate)


def _unit(tmp_path: Path, *, manifest: str, spider: str,
          provider: str | None = None) -> Path:
    root = tmp_path / "repo"
    d = root / "spiders" / "my-unit"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(textwrap.dedent(manifest), encoding="utf-8")
    (d / "spider.py").write_text(textwrap.dedent(spider), encoding="utf-8")
    if provider is not None:
        pkg = root / "fd_industry_data"
        pkg.mkdir()
        (pkg / "provider.py").write_text(textwrap.dedent(provider), encoding="utf-8")
    return root


def _run(root: Path) -> list[str]:
    return gate._declared_command_units(root)


class TestReachable:
    def test_entry_itself_passes(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
        """, spider="""
            def run_my_unit(limit: int = 10):
                return []
        """)
        assert _run(root) == []

    def test_direct_call_from_entry_passes(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
              - command: get_extra
        """, spider="""
            def get_extra(limit: int = 10):
                return []

            def run_my_unit(limit: int = 10):
                return get_extra(limit=limit)
        """)
        assert _run(root) == []

    def test_indirect_call_via_helper_passes(self, tmp_path):
        """入口 → 辅助函数 → 声明命令（调用闭包，非仅直接调用）。"""
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
              - command: get_extra
        """, spider="""
            def get_extra(limit: int = 10):
                return []

            def _helper(limit):
                return get_extra(limit=limit)

            def run_my_unit(limit: int = 10):
                return _helper(limit)
        """)
        assert _run(root) == []

    def test_provider_dispatched_passes(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
              - command: get_legacy
        """, spider="""
            def get_legacy(start_year: int = 2010):
                return []

            def run_my_unit(limit: int = 10):
                return []
        """, provider="""
            def run(self, command, params):
                if command == "get_legacy":
                    return get_legacy(**params)
        """)
        assert _run(root) == []

    def test_method_call_resolves_by_name(self, tmp_path):
        """self.x() 归一为 x，类内方法调用也算可达。"""
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
              - command: get_extra
        """, spider="""
            class C:
                def get_extra(self, limit=10):
                    return []

            def run_my_unit(limit: int = 10):
                return C().get_extra(limit=limit)
        """)
        assert _run(root) == []


class TestUnreachable:
    def test_orphan_command_flagged(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
              - command: get_orphan
        """, spider="""
            def get_orphan(limit: int = 10):
                return []

            def run_my_unit(limit: int = 10):
                return []
        """)
        hits = _run(root)
        assert len(hits) == 1 and "get_orphan" in hits[0]

    def test_golden_does_not_make_it_reachable(self, tmp_path):
        """有 golden 不算可达——这正是 eurostat 扩展躲过检查的原因。"""
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
              - command: get_orphan
        """, spider="""
            def get_orphan(limit: int = 10):
                return []

            def run_my_unit(limit: int = 10):
                return []
        """)
        (root / "spiders" / "my-unit" / "golden").mkdir()
        (root / "spiders" / "my-unit" / "golden" / "002-orphan.json").write_text(
            '{"sample_id": "002", "target": "get_orphan"}', encoding="utf-8")
        assert len(_run(root)) == 1

    def test_main_block_call_still_flagged(self, tmp_path):
        """__main__ 块是开发者手工路径，不是平台调度面。"""
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
              - command: get_orphan
        """, spider="""
            def get_orphan(limit: int = 10):
                return []

            def run_my_unit(limit: int = 10):
                return []

            if __name__ == "__main__":
                get_orphan(limit=5)
        """)
        hits = _run(root)
        assert len(hits) == 1 and "get_orphan" in hits[0]

    def test_dynamic_call_target_flagged(self, tmp_path):
        """动态派发不可静态解析 → 报违规（宁可显式写出调用）。"""
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
              - command: get_orphan
        """, spider="""
            def get_orphan(limit: int = 10):
                return []

            def run_my_unit(limit: int = 10):
                for fn in (get_orphan,):
                    return fn(limit=limit)
        """)
        hits = _run(root)
        assert len(hits) == 1 and "get_orphan" in hits[0]


class TestScope:
    def test_old_schema_skipped(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            commands:
              - command: "python spider.py --x"
        """, spider="""
            def run_my_unit(limit: int = 10):
                return []
        """)
        assert _run(root) == []

    def test_no_provider_file_tolerated(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
        """, spider="""
            def run_my_unit(limit: int = 10):
                return []
        """)
        assert _run(root) == []

    def test_syntax_error_does_not_crash(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
        """, spider="""
            def run_my_unit(limit: int = 10)
                return []
        """)
        assert _run(root) == []  # 入口自身，不依赖解析结果


class TestRealTree:
    def test_repo_tree_is_clean(self):
        """当前内容仓应零违规——eurostat-api 四条扩展已接入入口。"""
        assert gate._declared_command_units(REPO) == []
