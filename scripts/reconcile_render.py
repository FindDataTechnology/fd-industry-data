#!/usr/bin/env python3
"""Read-only reconciliation: manifest schedule <-> rendered CronJob <-> source row.

Change harden-crawl-delivery 1.5. Three assertions, by site kind:

1. **k8s sites** (``site`` absent or a registry entry with ``kind: k8s``): a
   non-empty ``schedule`` requires a CronJob whose ``fd-industry/source`` label
   matches the directory name. The reverse also holds: a CronJob in the
   namespace whose source has no scheduled manifest is an orphan.
2. **docker sites** (``kind: docker``): NO CronJob is expected — cadence is
   owned by that site's dispatcher tick. Asserting a CronJob here is the bug
   this script exists to prevent: ``eastmoney-data``/``eastmoney-futures``/
   ``worldsteel`` have no CronJob *by design* and run 45/29/3 times a month.
3. **source rows** (optional, needs a DSN): every scheduled source has a
   ``crawl_sources`` row and it agrees on site. Federated members are owned by
   their registration seed, not by manifests, so they are out of scope.

Writes nothing. Exit 1 when any violation is found, 0 when clean.

Usage:
    python3 scripts/reconcile_render.py                      # cluster only
    python3 scripts/reconcile_render.py --json
    python3 scripts/reconcile_render.py --db --dsn "$FD_CRAWL_DB_URL"
    python3 scripts/reconcile_render.py --content-dir /tmp/fixture --skip-cluster
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from fd_industry_data.sites import DEFAULT_SITE, load_sites  # noqa: E402

DEFAULT_NAMESPACE = "fd-industry"
DEFAULT_CONTEXT = "tencent"


def scheduled_manifests(content_dir: Path) -> dict[str, dict]:
    """{source: {schedule, site, kind, stopped}} for every schedulable manifest.

    ``stopped`` means parked/disabled: the renderer keeps a ``suspend: true``
    CronJob instead of emitting nothing, because ArgoCD refuses an auto-sync
    that would empty the app ("auto-sync will wipe out all resources") and the
    old CronJob then keeps firing — bls / cncotton were doing exactly that.
    """
    sites = load_sites()
    out: dict[str, dict] = {}
    for m in sorted(content_dir.glob("*/manifest.yaml")):
        try:
            data = yaml.safe_load(m.read_text()) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        schedule = str(data.get("schedule") or "").strip()
        stopped = data.get("parked") is True or data.get("enabled") is False
        if not schedule and not stopped:
            continue
        site = str(data.get("site") or DEFAULT_SITE)
        out[m.parent.name] = {
            "schedule": " ".join(schedule.split()),
            "site": site,
            "kind": (sites.get(site) or {}).get("kind", "k8s"),
            "stopped": stopped,
        }
    return out


def cluster_cronjobs(context: str, namespace: str) -> dict[str, dict]:
    """{source label: {schedule, suspend}} for CronJobs in the namespace."""
    proc = subprocess.run(
        ["kubectl", "--context", context, "-n", namespace, "get", "cronjob", "-o", "json"],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"kubectl failed: {proc.stderr.strip()[:400]}")
    out: dict[str, dict] = {}
    for item in json.loads(proc.stdout).get("items", []):
        labels = (item.get("metadata") or {}).get("labels") or {}
        source = labels.get("fd-industry/source")
        if not source:
            continue
        spec = item.get("spec") or {}
        out[source] = {
            "schedule": (spec.get("schedule") or "").strip(),
            "suspend": bool(spec.get("suspend", False)),
        }
    return out


def db_source_rows(dsn: str) -> dict[str, str]:
    """{source: site} for platform rows; federated rows excluded."""
    import psycopg2

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT source, site FROM crawl_sources WHERE kind = 'platform'")
        return {s: site for s, site in (cur.fetchall() or [])}


def reconcile(manifests: dict[str, dict], cronjobs: dict[str, str] | None,
              rows: dict[str, str] | None) -> list[dict]:
    violations: list[dict] = []

    if cronjobs is not None:
        for source, meta in sorted(manifests.items()):
            if meta["kind"] == "docker":
                continue  # cadence owned by the site's dispatcher tick
            live = cronjobs.get(source)
            if meta["stopped"]:
                if live is None:
                    # Steady state for a source parked before it was ever lit:
                    # nothing to stop, so this is advice, not a violation.
                    violations.append({
                        "kind": "missing-suspended-cronjob", "source": source,
                        "severity": "warn",
                        "detail": "已停用，集群无对应工作负载（无东西需要停）",
                        "fix": "无需处理；同步后会出现 suspend: true 的占位 CronJob",
                    })
                elif not live["suspend"]:
                    violations.append({
                        "kind": "parked-but-active", "source": source,
                        "detail": f"manifest 已停用但 CronJob 仍在调度 schedule={live['schedule']}",
                        "fix": "按裁决 A 渲染 suspend: true（需发布 chart/appset）；止血可 kubectl patch suspend",
                    })
                continue
            if live is None:
                violations.append({
                    "kind": "missing-cronjob", "source": source,
                    "detail": f"schedule={meta['schedule']} site={meta['site']}",
                    "fix": "让 ApplicationSet 同步该源；若其 site 实为 docker 请改 manifest",
                })
            elif live["schedule"] and live["schedule"] != meta["schedule"]:
                violations.append({
                    "kind": "schedule-mismatch", "source": source,
                    "detail": f"manifest={meta['schedule']} cluster={live['schedule']}",
                    "fix": "等待同步；持续不一致则检查 ApplicationSet 渲染参数",
                })
        for source in sorted(set(cronjobs) - set(manifests)):
            violations.append({
                "kind": "orphan-cronjob", "source": source,
                "detail": f"cluster schedule={cronjobs[source]['schedule'] or '<empty>'}",
                "fix": "manifest 已无此源；确认后由 prune 回收",
            })

    if rows is not None:
        for source, meta in sorted(manifests.items()):
            if source not in rows:
                violations.append({
                    "kind": "missing-source-row", "source": source,
                    "detail": f"site={meta['site']}",
                    "fix": "等待 dispatcher 的 sync_sources 周期；持续缺失则查其日志",
                })
            elif rows[source] and rows[source] != meta["site"]:
                violations.append({
                    "kind": "site-mismatch", "source": source,
                    "detail": f"manifest={meta['site']} db={rows[source]}",
                    "fix": "等待同步；持续不一致则查 sync_sources",
                })

    return violations


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--content-dir", default=str(REPO / "spiders"))
    ap.add_argument("--context", default=DEFAULT_CONTEXT)
    ap.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    ap.add_argument("--skip-cluster", action="store_true")
    ap.add_argument("--db", action="store_true", help="also check crawl_sources rows")
    ap.add_argument("--dsn", default=os.environ.get("FD_CRAWL_DB_URL", ""))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv[1:])

    manifests = scheduled_manifests(Path(args.content_dir))
    cronjobs = None if args.skip_cluster else cluster_cronjobs(args.context, args.namespace)
    rows = None
    if args.db:
        if not args.dsn:
            print("reconcile_render: --db needs --dsn or FD_CRAWL_DB_URL", file=sys.stderr)
            return 2
        rows = db_source_rows(args.dsn)

    violations = reconcile(manifests, cronjobs, rows)
    errors = [v for v in violations if v.get("severity") != "warn"]
    if args.json:
        print(json.dumps({
            "scheduled_sources": len(manifests),
            "cluster_cronjobs": None if cronjobs is None else len(cronjobs),
            "db_rows": None if rows is None else len(rows),
            "violations": errors,
            "warnings": [v for v in violations if v.get("severity") == "warn"],
        }, ensure_ascii=False, indent=2))
    else:
        for v in violations:
            tag = "warn " if v.get("severity") == "warn" else ""
            print(f"{tag}{v['kind']}: {v['source']} — {v['detail']} | fix: {v['fix']}")
        print(f"reconcile_render: {len(manifests)} schedulable sources, "
              f"{len(errors)} violations, "
              f"{len(violations) - len(errors)} warnings"
              + (" (cluster skipped)" if cronjobs is None else "")
              + (" (db skipped)" if rows is None else ""))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
