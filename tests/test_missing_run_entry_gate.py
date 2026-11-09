"""缺失 run_<slug> 入口规则测试（conformance_gate `missing-run-entry`）。

契约：有 schedule 的 ``spiders/<slug>/`` 单元，其 spider.py 必须定义**模块级**
``run_<slug>``（slug 连字符→下划线）。runner_cli._load_entry 恒定只解析这一个
入口；多个具名 ``run_*`` 而无 ``run_<slug>`` 时判歧义 → 报到前 exit 4，30 天巡检
看不见（education 三源基线盲区）。AST 静态分析，不 import 单元；adapter /
loose / 无 schedule（parked）单元不适用。

正例：有入口（含多具名命令 + 包装入口、async、连字符 slug、嵌套 def 不算）→ 不报。
反例：无入口（只具名命令 / 无任何 run_* / 语法错 / spider.py 缺失）→ 报。
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


def _unit(tmp_path: Path, *, manifest: str, spider: str | None,
          slug: str = "my-unit", adapter: str | None = None) -> Path:
    """Materialize a minimal repo root with one spiders/<slug>/ unit."""
    root = tmp_path / "repo"
    d = root / "spiders" / slug
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(textwrap.dedent(manifest), encoding="utf-8")
    if spider is not None:
        (d / "spider.py").write_text(textwrap.dedent(spider), encoding="utf-8")
    if adapter is not None:
        ad = root / "fd_open_data_mcp" / "adapters"
        ad.mkdir(parents=True)
        (ad / "foo_adapter.py").write_text(textwrap.dedent(adapter), encoding="utf-8")
        (ad / "__init__.py").write_text("", encoding="utf-8")
    return root


def _run(root: Path) -> list[str]:
    return gate._missing_run_entry_units(root)


# 5-field cron + data_period keeps validate_manifests quiet for collect() tests
SCHEDULED = """\
    name: my-unit
    schedule: "0 4 * * *"
    data_period: month
"""
UNSCHEDULED = """\
    name: my-unit
    data_period: month
"""


class TestMissingRunEntryPasses:
    def test_entry_def_present(self, tmp_path):
        root = _unit(tmp_path, manifest=SCHEDULED, spider="""
            def run_my_unit(limit: int = 10):
                return []
        """)
        assert _run(root) == []

    def test_multiple_named_runs_plus_wrapper(self, tmp_path):
        """修复后的真实形态：多个具名命令 + 一个 run_<slug> 包装入口。"""
        root = _unit(tmp_path, manifest=SCHEDULED, spider="""
            def run_ocw_catalog(limit: int = 10):
                return []

            def run_openedx_catalog(limit: int = 10):
                return []

            def run_my_unit(limit: int = 10):
                return run_ocw_catalog(limit=limit)
        """)
        assert _run(root) == []

    def test_dash_slug_maps_to_underscore_entry(self, tmp_path):
        """slug 连字符→下划线：spiders/my-unit 认 run_my_unit。"""
        root = _unit(tmp_path, manifest=SCHEDULED, spider="""
            def run_my_unit(limit: int = 10):
                return []
        """, slug="my-unit")
        assert _run(root) == []

    def test_async_entry_counts(self, tmp_path):
        root = _unit(tmp_path, manifest=SCHEDULED, spider="""
            async def run_my_unit(limit: int = 10):
                return []
        """)
        assert _run(root) == []

    def test_unscheduled_unit_needs_no_entry(self, tmp_path):
        """parked / 未排期源从不被调度，不要求 run_<slug>。"""
        root = _unit(tmp_path, manifest=UNSCHEDULED, spider="""
            def run_other(limit: int = 10):
                return []
        """)
        assert _run(root) == []

    def test_empty_schedule_needs_no_entry(self, tmp_path):
        root = _unit(tmp_path, manifest="""
            name: my-unit
            schedule: ""
        """, spider="""
            def run_other(limit: int = 10):
                return []
        """)
        assert _run(root) == []


class TestMissingRunEntryFlagged:
    def test_only_named_runs_no_entry(self, tmp_path):
        """基线真缺陷形态：只有多个具名 run_*，没有 run_<slug>。"""
        root = _unit(tmp_path, manifest=SCHEDULED, spider="""
            def run_ocw_catalog(limit: int = 10):
                return []

            def run_openedx_catalog(limit: int = 10):
                return []
        """)
        hits = _run(root)
        assert len(hits) == 1
        assert "run_my_unit" in hits[0]
        assert "run_ocw_catalog" in hits[0] and "run_openedx_catalog" in hits[0]
        assert "schedule" in hits[0]

    def test_no_run_defs_at_all(self, tmp_path):
        root = _unit(tmp_path, manifest=SCHEDULED, spider="""
            def _helper():
                return []
        """)
        hits = _run(root)
        assert len(hits) == 1 and "run_my_unit" in hits[0]

    def test_dash_slug_without_underscore_entry_flagged(self, tmp_path):
        root = _unit(tmp_path, manifest=SCHEDULED, spider="""
            def run_alpha(limit: int = 10):
                return []
        """, slug="my-unit")
        hits = _run(root)
        assert len(hits) == 1 and "run_my_unit" in hits[0]

    def test_nested_def_is_not_an_entry(self, tmp_path):
        """模块级 def 才算入口：嵌套/类内 def 不满足 getattr(mod, run_<slug>)。"""
        root = _unit(tmp_path, manifest=SCHEDULED, spider="""
            class C:
                def run_my_unit(self, limit=10):
                    return []
        """)
        hits = _run(root)
        assert len(hits) == 1 and "run_my_unit" in hits[0]

    def test_syntax_error_does_not_crash(self, tmp_path):
        root = _unit(tmp_path, manifest=SCHEDULED, spider="""
            def run_my_unit(limit: int = 10)
                return []
        """)
        hits = _run(root)
        assert len(hits) == 1 and "syntax error" in hits[0]

    def test_missing_spider_py_flagged_via_units(self, tmp_path):
        """collect() 注入 units 路径：目录有 schedule 但无 spider.py。"""
        root = _unit(tmp_path, manifest=SCHEDULED, spider=None)
        units = [{"kind": "spider_dir", "path": "spiders/my-unit"}]
        hits = gate._missing_run_entry_units(root, units)
        assert len(hits) == 1 and "spider.py" in hits[0]


class TestScope:
    def test_adapter_type_not_applicable(self, tmp_path):
        """adapter 类型不适用：只有 scheduled spider 单元被报，adapter 不报。"""
        root = _unit(tmp_path, manifest=SCHEDULED, spider="""
            def run_alpha(limit: int = 10):
                return []
        """, adapter="""
            def build_params(spec):
                return spec
        """)
        hits = _run(root)
        assert len(hits) == 1
        assert "spiders/my-unit" in hits[0]
        assert not any("adapter" in h for h in hits)

    def test_fix_registered(self):
        """FIXES 必须有修法，否则 main() 报违规时 KeyError。"""
        assert gate.MISSING_RUN_ENTRY in gate.FIXES
        assert "run_<slug>" in gate.FIXES[gate.MISSING_RUN_ENTRY]


class TestCollectIntegration:
    def test_collect_reports_missing_entry(self, tmp_path):
        """真实门禁路径 collect() 也带上 missing-run-entry 违规。"""
        root = _unit(tmp_path, manifest=SCHEDULED, spider="""
            def run_alpha(limit: int = 10):
                return []
        """)
        violations, _count = gate.collect([root])
        assert any(vtype == gate.MISSING_RUN_ENTRY and "run_my_unit" in path
                   for vtype, path in violations)

    def test_collect_clean_when_entry_present(self, tmp_path):
        root = _unit(tmp_path, manifest=SCHEDULED, spider="""
            def run_my_unit(limit: int = 10):
                return []
        """)
        violations, _count = gate.collect([root])
        assert not any(vtype == gate.MISSING_RUN_ENTRY for vtype, _ in violations)


class TestRealTree:
    def test_repo_tree_is_clean(self):
        """当前内容仓应零违规——education 三源入口已补齐。"""
        assert gate._missing_run_entry_units(REPO) == []
