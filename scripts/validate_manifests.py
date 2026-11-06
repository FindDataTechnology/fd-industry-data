#!/usr/bin/env python3
"""Validate spiders/<src>/manifest.yaml against the v2 scheduling contract.

v2 fields are all optional (v1 manifests stay legal):
- schedule: 5-field cron expression; empty/absent = not scheduled
- data_period: data natural cycle (day/month/year/irregular); consumed by the
  health inspector for overdue judgment (add-fleet-lifecycle-adjudication)
- memory_limit / cpu_limit: k8s quantities (defaults applied by the chart)
- enabled: bool; false suppresses scheduling even with a schedule
- site: registered run-site id (fd_industry_data/sites.yaml); absent = tencent

Exit 0 when every manifest is valid; otherwise list per-file violations.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fd_industry_data.sites import load_sites  # noqa: E402

QUANTITY_RE = re.compile(r"^\d+(?:\.\d+)?(?:m|k|M|G|Ki|Mi|Gi)?$")
CRON_FIELD_RE = re.compile(r"^[0-9*/,\-A-Za-z]+$")

# data_period 合法值（add-fleet-lifecycle-adjudication 1.1）：数据自然周期四值枚举
DATA_PERIODS = ("day", "month", "year", "irregular")


def _check_cron(expr: str) -> str | None:
    fields = expr.split()
    if len(fields) != 5:
        return f"schedule '{expr}' must have 5 cron fields, got {len(fields)}"
    for f in fields:
        if not CRON_FIELD_RE.match(f):
            return f"schedule '{expr}' has invalid field '{f}'"
    return None


def _check_quantity(name: str, val: str) -> str | None:
    if not isinstance(val, str) or not QUANTITY_RE.match(val):
        return f"{name} {val!r} is not a k8s quantity (e.g. 384Mi, 300m)"
    return None


def validate(manifest_path: Path) -> list[str]:
    errs: list[str] = []
    try:
        data = yaml.safe_load(manifest_path.read_text())
    except yaml.YAMLError as e:
        return [f"{manifest_path}: unparseable YAML: {e}"]
    if not isinstance(data, dict):
        return [f"{manifest_path}: manifest is not a mapping"]

    schedule = data.get("schedule")
    if schedule is not None:
        if not isinstance(schedule, str) or not schedule.strip():
            return [f"{manifest_path}: schedule must be a non-empty cron string when present"]
        if (e := _check_cron(schedule.strip())):
            errs.append(f"{manifest_path}: {e}")

    data_period = data.get("data_period")
    if data_period is not None:
        if not isinstance(data_period, str) or data_period.strip().lower() not in DATA_PERIODS:
            errs.append(
                f"{manifest_path}: data_period {data_period!r} must be one of "
                f"{'/'.join(DATA_PERIODS)}"
            )

    # 有 schedule 且未 parked 的源必须声明 data_period（add-fleet-lifecycle-adjudication 1.4）：
    # 健康巡检依赖它判超期；静默源（schedule 空/缺）与 parked 源豁免
    scheduled = bool(schedule and isinstance(schedule, str) and schedule.strip())
    if scheduled and not data.get("parked") and data_period is None:
        errs.append(
            f"{manifest_path}: scheduled source must declare data_period "
            f"(one of {'/'.join(DATA_PERIODS)}); add it or set parked: true"
        )

    for q in ("memory_limit", "cpu_limit"):
        if (v := data.get(q)) is not None and (e := _check_quantity(q, v)):
            errs.append(f"{manifest_path}: {e}")

    enabled = data.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        errs.append(f"{manifest_path}: enabled must be a bool, got {enabled!r}")

    profile = data.get("auth_profile")
    if profile is not None and (not isinstance(profile, str) or not profile.strip()):
        errs.append(f"{manifest_path}: auth_profile must be a non-empty name when present")

    site = data.get("site")
    if site is not None:
        if not isinstance(site, str) or not site.strip():
            errs.append(f"{manifest_path}: site must be a non-empty site id when present")
        elif site.strip() not in (sites := load_sites()):
            errs.append(
                f"{manifest_path}: site {site!r} is not registered "
                f"(known: {', '.join(sorted(sites))}); "
                "register it in fd_industry_data/sites.yaml first"
            )

    return errs


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1] / "spiders"
    manifests = sorted(root.glob("*/manifest.yaml"))
    if not manifests:
        print(f"validate_manifests: no manifests under {root}", file=sys.stderr)
        return 1
    errs: list[str] = []
    for m in manifests:
        errs.extend(validate(m))
    for e in errs:
        print(e, file=sys.stderr)
    print(f"validate_manifests: {len(manifests)} manifests, {len(errs)} violations")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
