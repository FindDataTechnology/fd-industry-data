# fd-industry-data scripts

Maintenance and verification tooling for the crawler estate. All scanners here
are read-only over the scanned tree unless a flag explicitly says otherwise.

## Triage audit — `triage_audit.py`

Read-only inventory of every generated crawler unit (spider directories, loose
`*_spider.py` files, adapters, abandoned `scraw-*` scaffolds) across
`fd-industry-data` and `fd-open-data-mcp`, classifying each into L1–L4 by static
evidence and emitting a triage report plus a derived cleanup backlog.
Adapter modules wired into `adapters/__init__.py` (register-style param-mapping
registrars that intentionally never fetch) carry `register_style: true`, skip
the L4-fake branch, and are excluded from delete-candidate derivation.

```bash
python3 scripts/triage_audit.py --self-test   # fixture classification tests
python3 scripts/triage_audit.py --dry-run     # inventory counts, no outputs written
python3 scripts/triage_audit.py               # full report -> output/triage/
```

## Conformance gate — `conformance_gate.py`

The audit's enforcement twin: imports `triage_audit`'s discovery (same
heuristics, same ignore lists) and fails a tree that violates the standard
layout. Every batch crawler-generation session must run it and pass before
declaring completion (AGENTS.md, execution preference). Writes nothing.

```bash
python3 scripts/conformance_gate.py           # from fd-industry-data/
python3 fd-industry-data/scripts/conformance_gate.py   # from the finddata root
```

Fails (exit 1, one line per violation with its fix) on: a spider unit outside
`spiders/<slug>/` (including misspelled directories like `spiers/`), a
standard-layout unit lacking `manifest.yaml`, a loose `*_spider.py` outside a
unit directory, an empty non-standard top-level directory, or two adapters
differing only by dash-versus-underscore naming. A clean tree exits 0 with a
unit-count summary. `--roots <dir>[,<dir>]` scans a fixture tree instead.

## Cleanup execution — `execute_cleanup.py`

Archives targets read exclusively from the pinned cleanup backlog (never
re-derives) into `archive/triage-cleanup-<date>/` with `INDEX.md`/`index.json`.
Dry-run by default; `--apply` executes.

## Dead-manifest archival — `archive_dead_manifests.py`

Archives the `dead`-classified legacy manifests from the triage report
(`output/manifest-drafts/legacy_triage.json`) into
`archive/legacy-manifests-<date>/` with an evidence index; `alive`/`unknown`
manifests are verified byte-identical and a re-run is a no-op. Dry-run by
default; `--apply` executes.

## Manifest 命令漂移检查 — `check_manifest_commands.py`

只读 lint：逐个导入 `spiders/<slug>/spider.py`，校验 `functions[].command` 指向真实
存在的符号，并提示平台约定入口 `run_<slug>(limit)` 是否存在（runners.py 约定）。
发现漂移退出码非零。背景：2026-10-04 修掉 20 处存量模板漂移
（`get_<x>_data` → 真实 `run_<slug>`，含 cisa/nbs-stats/kitco/metal-com 等），
此类问题 `validate_manifests` / conformance gate 不覆盖（只查布局与调度字段）。

```bash
python3 scripts/check_manifest_commands.py    # 0 = 无漂移
```

**已收口（2026-10-04，change `dormant-unit-hygiene`）**：`nbs_gdp` 已规范（新增
`run_nbs_gdp` 包装；`get_macro_data`/`get_gdp_quarterly` 原名保留，供 provider/catalog
的 NBS MCP 命令面使用）；`flowers-yunnan` 等 14 个休眠单元经三路实跑证据定性后归档至
`archive/triage-cleanup-20261004/spiders/`（逐单元证据见其 INDEX.md）。本 lint 此后应为
0 问题；新增单元请保持 `run_<slug>(limit)` 约定。

## Other

- `generate_manifests.py` / `register_manifests.py` / `import_manifest.py` — manifest drafting pipeline (protocol: `output/manifest-drafts/MANIFEST-PROTOCOL.md`).
- `verify_l3_units.py`, `verify_commodity_spiders.py`, `run_commodity_spiders.py`, `test_commodity_spiders.py` — targeted live verification helpers.
