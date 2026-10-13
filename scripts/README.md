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

## 声明命令可达性 — `conformance_gate.py` 的 `declared-command-reachability` 规则

准入门规则（同为 AST 静态分析）：manifest `functions[].command` 声明的每个命令，必须
(a) 就是 `run_<slug>` 入口本身，(b) 在入口的**调用闭包**内（跨该单元所有 `.py`；
`self.x()` 归一为 `x`），或 (c) 在 `fd_industry_data/provider.py` 的分派表中。三者皆非
→ 违规。

背景（2026-10-13，change `declared-command-reachability`）：`runner_cli._load_entry` 恒定
只加载 `run_<src>`，**没有第二个入口**，所以一个声明命令若不在入口调用链上就是死声明。
`eurostat-api` 声明 5 个命令，其中 4 个（Wave C 扩展 c20/bsci/apri/comext）各有 golden
且健康检查全绿，但入口只遍历三条基础数据集——**`crawl_items` 里它们一行都没有**。
golden 只证明「函数能跑」，不证明「生产会调用它」；本规则正是补上这个缺口。

判定取**调用闭包**而非仅直接调用（辅助函数间接调用是真实现象）；
**`__main__` 块内的调用不算平台调用面**——那是开发者手工 CLI 路径，算进去就等于用
「本地能跑」冒充「生产在跑」。
**动态调用目标（`for fn in (...)` / `getattr`）不可静态解析，会报违规**；修法是显式
按名调用（见 `eurostat-api._wave_c_rows`）。

```bash
python3 scripts/conformance_gate.py --roots .   # 0 = 声明的命令都可被平台调用
```

## 声明参数可达性 — `conformance_gate.py` 的 `declared-param-reachability` 规则

准入门规则（AST 静态分析；gate 阶段只装 pyyaml，故不 import 单元）：manifest
`functions[].parameters` 里声明的每个名字，必须在该 `command` 的 `def` 签名中**显式
出现**。`parameters` 与 `columns` 都必须是列表形（字典形会被协议加载器
`load_catalog` 直接拒绝）。

口径与 `runner_cli._split_params` 逐字一致——**`*args`/`**kwargs` 不算声明通道**：
若把 `**kwargs` 算作可达，manifest 拼错键将永远静默通过。规则**单向**：只查「声明了
但入口收不到」，不查「入口可收但未声明」（回填类参数按 `legal-line-federation`
裁决刻意不上触发面）。

背景（2026-10-13，change `declared-param-reachability`）：`--params` 通道修通后做全量
审计，查出 5 处声明不可达（`inmetals`/`kitco`/`metal-com` 的 `urls`；`nbs-stats` 的
`categories`/`start_year`）与 `nbs-stats` 的字典形声明（导致该在产单元**无法被协议
加载**）。生产证据：`pending_runs` 有 15 行带 `window: "12m"` 落在既未声明也未接受它
的单元上，全部报 `done`——请求被无声忽略且不留痕迹。丢弃现在写入
`crawl_runs.metrics.dropped_params`。

```bash
python3 scripts/conformance_gate.py --roots .   # 0 = 声明与入口一致
```

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
