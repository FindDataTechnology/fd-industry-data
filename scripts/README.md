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

## Other

- `generate_manifests.py` / `register_manifests.py` / `import_manifest.py` — manifest drafting pipeline (protocol: `output/manifest-drafts/MANIFEST-PROTOCOL.md`).
- `verify_l3_units.py`, `verify_commodity_spiders.py`, `run_commodity_spiders.py`, `test_commodity_spiders.py` — targeted live verification helpers.
