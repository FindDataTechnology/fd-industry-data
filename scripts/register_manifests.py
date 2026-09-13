#!/usr/bin/env python3
"""Register placed manifests and triage legacy manifests."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
FINDDATA_ROOT = REPO_ROOT.parent
MCP_ROOT = FINDDATA_ROOT / "fd-open-data-mcp"
DEFAULT_DATABASE_URL = f"sqlite:///{MCP_ROOT / 'metadata' / 'daas.db'}"
DRAFTS = REPO_ROOT / "output" / "manifest-drafts"
PLACEMENT_REPORT = DRAFTS / "placement_report.json"
MANIFESTS_DIR = REPO_ROOT / "manifests"
REV2_REGISTRY = REPO_ROOT / "output" / "l3-reanalysis" / "revision-2" / "registry.json"
TRIAGE_REPORT = DRAFTS / "legacy_triage.json"
REGISTRATION_REPORT = DRAFTS / "registration_report.json"
EXPECTED_PLACED = 9
EXPECTED_FUNCTIONS = 33
RETAINED_LEGACY_SOURCES = {"akshare"}
MULTI_SUFFIXES = {"com.cn", "org.cn", "net.cn", "gov.cn", "edu.cn", "ac.cn", "co.jp", "co.uk", "org.uk", "gov.uk", "com.au", "com.hk", "com.tw", "org.hk"}
HOST_PREFIX_STRIP = {"www", "data", "m", "en", "mp"}
ALIVE_VERDICTS = {"verified_api", "verified_html", "degraded"}
DEAD_VERDICTS = {"unreachable"}


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from exc


def placement_entries() -> list[str]:
    report = load_json(PLACEMENT_REPORT)
    entries = report.get("placed", []) + report.get("already_placed", [])
    names = [entry.get("manifest") for entry in entries if isinstance(entry, dict)]
    names = [name for name in names if name]
    if len(names) != EXPECTED_PLACED:
        raise SystemExit(f"placement report must list exactly {EXPECTED_PLACED} manifests; found {len(names)}")
    if len(set(names)) != len(names):
        raise SystemExit("placement report contains duplicate manifest names")
    return names


def load_manifests(names: list[str]) -> list[tuple[Path, object]]:
    from fd_open_data_protocol.loader import load_catalog

    manifests = []
    for name in names:
        path = MANIFESTS_DIR / name
        if not path.is_file():
            raise SystemExit(f"manifest listed by placement report is missing: {path}")
        manifests.append((path, load_catalog(path)))
    return manifests


def redact_database_url(url: str) -> str:
    if "://" not in url:
        return "***"
    parsed = urlparse(url)
    if parsed.password:
        username = parsed.username or ""
        return url.replace(f"{username}:{parsed.password}@", f"{username}:***@")
    return url


def effective_database_url(explicit: str | None) -> str:
    return explicit or os.environ.get("FD_OPEN_DATA_MCP_DATABASE_URL", DEFAULT_DATABASE_URL)


def dry_run(manifests: list[tuple[Path, object]]) -> int:
    print(f"dry-run: {len(manifests)} manifests")
    for path, manifest in manifests:
        print(f"  {manifest.name}: {path.name} ({len(manifest.functions)} functions)")
    return 0


def source_snapshot(session) -> dict[str, dict]:
    from fd_open_data_mcp.models import Function, Source

    return {
        source.name: {
            "label": source.label,
            "url": source.url,
            "functions": {
                function.command: {column.name for column in function.columns}
                for function in source.functions
            },
        }
        for source in session.query(Source).all()
    }


def manifest_snapshot(manifest) -> dict:
    return {
        "label": manifest.label,
        "url": manifest.source_url,
        "functions": {
            function.command: {column.name for column in function.columns}
            for function in manifest.functions
        },
    }


def mismatch(name: str, expected: dict, found: dict | None, allow_extra_functions: bool = False) -> str | None:
    if found is None:
        return f"{name}: expected source, found none"
    if expected["label"] != found["label"]:
        return f"{name}: label expected={expected['label']!r} found={found['label']!r}"
    if expected["url"] != found["url"]:
        return f"{name}: url expected={expected['url']!r} found={found['url']!r}"
    expected_functions = expected["functions"]
    found_functions = found["functions"]
    missing_functions = set(expected_functions) - set(found_functions)
    extra_functions = set(found_functions) - set(expected_functions)
    if missing_functions or (extra_functions and not allow_extra_functions):
        return (
            f"{name}: functions expected-only={sorted(missing_functions)} "
            f"found-only={sorted(extra_functions)}"
        )
    for command, expected_columns in expected_functions.items():
        found_columns = found_functions[command]
        if expected_columns != found_columns:
            return (
                f"{name}/{command}: columns expected-only={sorted(expected_columns - found_columns)} "
                f"found-only={sorted(found_columns - expected_columns)}"
            )
    return None


def verify_source(session, manifest, allow_extra_functions: bool = False) -> None:
    from fd_open_data_mcp.models import Source

    source = session.query(Source).filter_by(name=manifest.name).first()
    if source is None:
        raise SystemExit(f"verification failed: source not found: {manifest.name}")
    expected = manifest_snapshot(manifest)
    found = {
        "label": source.label,
        "url": source.url,
        "functions": {
            function.command: {column.name for column in function.columns}
            for function in source.functions
        },
    }
    error = mismatch(manifest.name, expected, found, allow_extra_functions=allow_extra_functions)
    if error:
        raise SystemExit(f"verification failed: {error}")


def register_and_verify(database_url: str, manifests: list[tuple[Path, object]]) -> dict:
    from fd_open_data_mcp.catalog.register import register_datasource
    from fd_open_data_mcp.db import get_database
    from fd_open_data_mcp.models import Function, Source

    database = get_database(database_url)

    def register_pass() -> list[dict]:
        summaries = []
        for path, manifest in manifests:
            session = database.get_session()
            try:
                summary = register_datasource(manifest, session)
            finally:
                session.close()
            declared = manifest_snapshot(manifest)
            declared_functions = len(declared["functions"])
            declared_columns = sum(len(columns) for columns in declared["functions"].values())
            summaries.append({
                "manifest": path.name,
                **summary,
                "declared_functions": declared_functions,
                "declared_columns": declared_columns,
            })
            if not summaries or len(summaries) == 1:
                print(
                    f"registered {manifest.name}: {summary['functions']} inserted / "
                    f"{declared_functions} declared functions, {summary['columns']} inserted / "
                    f"{declared_columns} declared columns"
                )
        return summaries

    declared_function_count = sum(len(manifest_snapshot(manifest)["functions"]) for _path, manifest in manifests)
    if declared_function_count != EXPECTED_FUNCTIONS:
        raise SystemExit(f"expected {EXPECTED_FUNCTIONS} declared functions, found {declared_function_count}")

    first_pass = register_pass()
    session = database.get_session()
    try:
        first = source_snapshot(session)
    finally:
        session.close()

    register_pass()

    session = database.get_session()
    try:
        for _path, manifest in manifests:
            verify_source(
                session,
                manifest,
                allow_extra_functions=manifest.name in RETAINED_LEGACY_SOURCES,
            )
        registered = session.query(Source).filter(
            Source.name.in_([manifest.name for _path, manifest in manifests])
        ).all()
        function_count = session.query(Function).filter(
            Function.source_id.in_([source.id for source in registered])
        ).count()
        for source in registered:
            commands = [function.command for function in source.functions]
            if len(commands) != len(set(commands)):
                raise SystemExit(f"idempotency check failed: duplicate functions in {source.name}")
        second = source_snapshot(session)
    finally:
        session.close()

    if len(registered) != EXPECTED_PLACED:
        raise SystemExit(f"expected {EXPECTED_PLACED} registered sources, found {len(registered)}")
    if function_count < EXPECTED_FUNCTIONS:
        raise SystemExit(f"expected at least {EXPECTED_FUNCTIONS} catalog functions, found {function_count}")

    registered_names = {source.name for source in registered}
    changed = [
        name for name in sorted(registered_names)
        if first.get(name) != second.get(name)
    ]
    if changed:
        raise SystemExit(f"idempotency check failed: sources changed on re-registration: {changed}")

    report = {
        "database_url": redact_database_url(database_url),
        "manifests": first_pass,
        "sources": len(registered),
        "declared_functions": EXPECTED_FUNCTIONS,
        "functions": function_count,
        "idempotent": True,
    }
    REGISTRATION_REPORT.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {REGISTRATION_REPORT.relative_to(REPO_ROOT)}")
    return report


def url_host(url: str) -> str | None:
    if not url or "://" not in url:
        return None
    try:
        host = urlparse(url).hostname
    except ValueError:
        return None
    return host.lower().rstrip(".") if host else None


def canon_domain(host: str | None) -> str | None:
    if not host or host in {"localhost", "127.0.0.1"} or host.replace(".", "").isdigit():
        return None
    parts = host.split(".")
    while len(parts) > 2 and parts[0] in HOST_PREFIX_STRIP:
        parts = parts[1:]
    if ".".join(parts[-2:]) in MULTI_SUFFIXES and len(parts) >= 3:
        candidate = ".".join(parts[-3:])
    elif len(parts) >= 2:
        candidate = ".".join(parts[-2:])
    else:
        return None
    if candidate in {"com", "org", "net", "gov", "edu", "cn", "io", "info"}:
        return None
    return candidate


def load_revision2_units() -> dict[str, dict]:
    if not REV2_REGISTRY.is_file():
        raise SystemExit(f"missing revision-2 registry: {REV2_REGISTRY}")
    return load_json(REV2_REGISTRY).get("units", {})


def legacy_files(placed_names: list[str]) -> list[Path]:
    placed = set(placed_names)
    return sorted(path for path in MANIFESTS_DIR.glob("*.yaml") if path.name not in placed)


def load_legacy_manifest(path: Path) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("PyYAML is required to read legacy manifests") from exc

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("name") or not data.get("source_url"):
        raise SystemExit(f"legacy manifest is missing name or source_url: {path}")
    return data


def triage_legacy(placed_names: list[str]) -> dict:
    units = load_revision2_units()
    domain_units: dict[str, list[dict]] = defaultdict(list)
    for unit in units.values():
        endpoint = unit.get("endpoint") or ""
        domain = canon_domain(url_host(endpoint))
        if domain:
            domain_units[domain].append(unit)

    records = []
    before = {}
    for path in legacy_files(placed_names):
        data = path.read_bytes()
        before[path.name] = hashlib.sha256(data).hexdigest()
        manifest = load_legacy_manifest(path)
        domains = {canon_domain(url_host(manifest["source_url"]))}
        domains.discard(None)
        matches = [unit for domain in sorted(domains) for unit in domain_units.get(domain, [])]
        verdicts = {unit.get("verdict") for unit in matches}
        if verdicts & ALIVE_VERDICTS:
            verdict = "alive"
        elif verdicts and verdicts <= DEAD_VERDICTS:
            verdict = "dead"
        else:
            verdict = "unknown"
        records.append({
            "manifest": path.name,
            "name": manifest["name"],
            "source_url": manifest["source_url"],
            "domains": sorted(domains),
            "matched_units": sorted({unit.get("unit") for unit in matches if unit.get("unit")}),
            "verdict": verdict,
        })

    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in legacy_files(placed_names)}
    unchanged = before == after
    if not unchanged:
        raise SystemExit("legacy manifest bytes changed during triage")
    summary = {
        "total": len(records),
        "by_verdict": {verdict: sum(record["verdict"] == verdict for record in records)
                       for verdict in ("alive", "dead", "unknown")},
        "legacy_unchanged": unchanged,
        "registered_legacy": [],
    }
    report = {"summary": summary, "manifests": records}
    TRIAGE_REPORT.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {TRIAGE_REPORT.relative_to(REPO_ROOT)}: {summary}")
    return report


def run(database_url: str | None, dry: bool) -> int:
    names = placement_entries()
    manifests = load_manifests(names)
    if dry:
        return dry_run(manifests)

    effective = effective_database_url(database_url)
    print(f"database: {redact_database_url(effective)}")
    report = register_and_verify(effective, manifests)
    triage = triage_legacy(names)
    legacy_names = {record["name"] for record in triage["manifests"]}
    registered_names = {manifest.name for _path, manifest in manifests}
    overlap = sorted(legacy_names & registered_names)
    if overlap:
        raise SystemExit(f"legacy manifests were registered: {overlap}")
    print(f"registered {report['sources']} sources / {report['functions']} functions")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="override FD_OPEN_DATA_MCP_DATABASE_URL")
    parser.add_argument("--dry-run", action="store_true", help="list placed manifests without writing the catalog")
    args = parser.parse_args()
    return run(args.database_url, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
