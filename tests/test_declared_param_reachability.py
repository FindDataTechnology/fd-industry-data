"""声明参数可达性规则测试（conformance_gate `declared-param-reachability`）。

口径（与 runner_cli._extra_params 逐字一致）：只认签名里**显式命名**的参数，
`**kwargs` 不算。规则单向——只查「声明了但入口收不到」，不查「收得到但没声明」
（后者可能是刻意的产品裁决，如回填类参数不上触发面）。
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


def _unit(tmp_path: Path, *, manifest: str, spider: str) -> Path:
    """Materialize a minimal repo root with one unit under spiders/."""
    root = tmp_path / "repo"
    d = root / "spiders" / "my-unit"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(textwrap.dedent(manifest), encoding="utf-8")
    (d / "spider.py").write_text(textwrap.dedent(spider), encoding="utf-8")
    return root


def _run(root: Path) -> list[str]:
    return gate._declared_param_units(root)


class TestReachability:
    def test_reachable_param_passes(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
                parameters:
                  - name: limit
                    type: int
                  - name: scope
                    type: str
        """, spider="""
            def run_my_unit(limit: int = 10, scope: str = "monthly"):
                return []
        """)
        assert _run(root) == []

    def test_declared_but_not_accepted_is_flagged(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
                parameters:
                  - name: urls
                    type: list
        """, spider="""
            def run_my_unit(limit: int = 10):
                return []
        """)
        hits = _run(root)
        assert len(hits) == 1
        assert "urls" in hits[0] and "run_my_unit" in hits[0]

    def test_var_keyword_does_not_count(self, tmp_path):
        """**kwargs 会吞下任何键；若算可达，manifest 拼错键将永远通过。"""
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
                parameters:
                  - name: scope
                    type: str
        """, spider="""
            def run_my_unit(limit: int = 10, **_):
                return []
        """)
        hits = _run(root)
        assert len(hits) == 1 and "scope" in hits[0]

    def test_undeclared_but_accepted_is_not_flagged(self, tmp_path):
        """单向：入口可收但未声明不报违规（可能是刻意的产品裁决）。"""
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
                parameters:
                  - name: limit
                    type: int
        """, spider="""
            def run_my_unit(limit: int = 10, days: int = 3):
                return []
        """)
        assert _run(root) == []

    def test_keyword_only_param_counts(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
                parameters:
                  - name: scope
                    type: str
        """, spider="""
            def run_my_unit(limit: int = 10, *, scope: str = "m"):
                return []
        """)
        assert _run(root) == []

    def test_args_kwargs_are_not_names(self, tmp_path):
        """*args/**kwargs 的名字不是参数名。"""
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
                parameters:
                  - name: extra
                    type: str
        """, spider="""
            def run_my_unit(limit: int = 10, *extra, **kw):
                return []
        """)
        hits = _run(root)
        assert len(hits) == 1 and "extra" in hits[0]

    def test_multiple_defs_union(self, tmp_path):
        """同名多 def 取并集：运行时命中哪个由条件决定，静态不猜。"""
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
                parameters:
                  - name: scope
                    type: str
        """, spider="""
            import sys

            if sys.platform == "win32":
                def run_my_unit(limit: int = 10):
                    return []
            else:
                def run_my_unit(limit: int = 10, scope: str = "m"):
                    return []
        """)
        assert _run(root) == []

    def test_async_def_counts(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
                parameters:
                  - name: scope
                    type: str
        """, spider="""
            async def run_my_unit(limit: int = 10, scope: str = "m"):
                return []
        """)
        assert _run(root) == []


class TestShapes:
    def test_dict_form_parameters_flagged(self, tmp_path):
        """协议加载器对字典形直接拒绝（pydantic list_type），故形状属契约。"""
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
                parameters:
                  limit:
                    type: int
        """, spider="""
            def run_my_unit(limit: int = 10):
                return []
        """)
        hits = _run(root)
        assert len(hits) == 1 and "mapping" in hits[0]

    def test_dict_form_columns_flagged(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
                parameters:
                  - name: limit
                    type: int
                columns:
                  title:
                    type: str
        """, spider="""
            def run_my_unit(limit: int = 10):
                return []
        """)
        hits = _run(root)
        assert len(hits) == 1 and "columns" in hits[0]

    def test_parameter_entry_without_name_flagged(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
                parameters:
                  - type: int
        """, spider="""
            def run_my_unit(limit: int = 10):
                return []
        """)
        hits = _run(root)
        assert len(hits) == 1 and "lacks a name" in hits[0]

    def test_string_parameter_entries_accepted(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
                parameters:
                  - limit
        """, spider="""
            def run_my_unit(limit: int = 10):
                return []
        """)
        assert _run(root) == []


class TestScope:
    def test_old_schema_skipped(self, tmp_path):
        """旧 schema（shell commands，无 functions）不在本契约内。"""
        root = _unit(tmp_path, manifest="""
            name: my-unit
            commands:
              - command: "python spider.py --urls <u>"
        """, spider="""
            def run_spider(urls=None, save_results=True):
                return []
        """)
        assert _run(root) == []

    def test_no_parameters_declared_passes(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
                description: no params
        """, spider="""
            def run_my_unit(limit: int = 10):
                return []
        """)
        assert _run(root) == []

    def test_declared_command_without_def_flagged(self, tmp_path):
        """声明面指向不存在的符号——比参数问题更硬，必须报。"""
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_nonexistent
                parameters:
                  - name: limit
                    type: int
        """, spider="""
            def run_my_unit(limit: int = 10):
                return []
        """)
        hits = _run(root)
        assert len(hits) == 1 and "no such def" in hits[0]

    def test_syntax_error_does_not_crash(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            functions:
              - command: run_my_unit
                parameters:
                  - name: limit
                    type: int
        """, spider="""
            def run_my_unit(limit: int = 10)
                return []
        """)
        # 无法解析的 def 视为不存在 → 报「声明了但没有该 def」，不抛异常
        hits = _run(root)
        assert len(hits) == 1 and "no such def" in hits[0]


class TestRealTree:
    def test_repo_tree_is_clean(self):
        """当前内容仓应零违规——规则上线时已修掉全部存量违规。"""
        assert gate._declared_param_units(REPO) == []
