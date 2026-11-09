#!/usr/bin/env python3
"""Validate spiders/<src>/manifest.yaml against the v2 scheduling contract.

v2 fields are all optional (v1 manifests stay legal):
- schedule: 5-field cron expression; empty/absent = not scheduled
- data_period: data natural cycle (day/month/year/irregular); consumed by the
  health inspector for overdue judgment (add-fleet-lifecycle-adjudication)
- memory_limit / cpu_limit: k8s quantities (defaults applied by the chart)
- enabled: bool; false suppresses scheduling even with a schedule
- site: registered run-site id (fd_industry_data/sites.yaml); absent = tencent
- schedule_tz: IANA timezone the cron is evaluated in (absent =
  Asia/Shanghai). Rendered into the CronJob's spec.timeZone and mirrored into
  crawl_sources so the dispatcher and the cluster agree; see change
  harden-crawl-delivery D1.

Exit 0 when every manifest is valid; otherwise list per-file violations.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fd_industry_data.sites import load_sites  # noqa: E402

QUANTITY_RE = re.compile(r"^\d+(?:\.\d+)?(?:m|k|M|G|Ki|Mi|Gi)?$")
CRON_FIELD_RE = re.compile(r"^[0-9*/,\-A-Za-z]+$")
# IANA 名形状：Area/Location，允许 UTC 这类单段名与 Etc/GMT+8 这类带符号名。
TZ_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)*$")
# 环境变量名形状：大写字母/数字/下划线，不以数字开头（与 shell 一致）。
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

# data_period 合法值（add-fleet-lifecycle-adjudication 1.1）：数据自然周期四值枚举
DATA_PERIODS = ("day", "month", "year", "irregular")

_TZ_DB: bool | None = None


def _tz_database_available() -> bool:
    """本机是否有可用的 tz 数据库。

    门禁容器只保证 pyyaml（conformance_gate 的注释）；缺 tzdata 时 ZoneInfo
    对**所有**名字都抛 ZoneInfoNotFoundError。那种情况下严格校验会把
    ``Asia/Shanghai`` 误判成非法，所以先探测一次已知有效的名字，
    探不到就退化为只校验形状——「验不了」不等于「非法」。
    """
    global _TZ_DB
    if _TZ_DB is None:
        try:
            ZoneInfo("UTC")
            _TZ_DB = True
        except Exception:
            _TZ_DB = False
    return _TZ_DB


def _check_timezone(val: object) -> str | None:
    if not isinstance(val, str) or not val.strip():
        return "schedule_tz must be a non-empty IANA timezone name when present"
    name = val.strip()
    if not TZ_NAME_RE.match(name):
        return f"schedule_tz {val!r} is not an IANA timezone name (e.g. Asia/Shanghai, UTC)"
    if not _tz_database_available():
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return f"schedule_tz {val!r} is not a known IANA timezone"
    return None


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

    # 排期时区（harden-crawl-delivery 1.1）：缺省 Asia/Shanghai，由渲染器写进
    # CronJob 的 spec.timeZone 并镜像到 crawl_sources。允许在点亮前就声明，
    # 因此不要求同时存在 schedule。
    if (tz := data.get("schedule_tz")) is not None and (e := _check_timezone(tz)):
        errs.append(f"{manifest_path}: {e}")

    # 回填窗口（harden-crawl-delivery 3.5）：只对「只取最新 N 条」的源声明。
    # 形如 24m / 2y（月/年，1..120 个月）；经既有 --window-start/--window-end 执行，
    # 不是新的跑批通道。
    if (bw := data.get("backfill_window")) is not None:
        ok = False
        if isinstance(bw, int) and not isinstance(bw, bool):
            ok = 1 <= bw <= 120
        elif isinstance(bw, str):
            import re as _re
            m = _re.match(r"^(\d{1,3})([my])$", bw.strip().lower())
            ok = bool(m) and 1 <= int(m.group(1)) * (12 if m.group(2) == "y" else 1) <= 120
        if not ok:
            errs.append(
                f"{manifest_path}: backfill_window {bw!r} must be months (1-120) "
                "or a string like '24m' / '2y'")

    # 数据期字段（harden-crawl-delivery 3.2）：条目里承载数据期的键名。只支持
    # 顶层键；声明后 runner 会据此记录水位，取不到时降级为 unavailable，不猜路径。
    if (fld := data.get("data_period_field")) is not None:
        if not isinstance(fld, str) or not fld.strip():
            errs.append(f"{manifest_path}: data_period_field must be a non-empty key name")
        elif "." in fld:
            errs.append(
                f"{manifest_path}: data_period_field {fld!r} must be a top-level key "
                "(nested paths are not supported)")

    # 运行配置依赖（harden-crawl-delivery 2.5）：只写名字，不写值。缺失时 runner
    # 预检判 blocked-config，不取数、不进失败率。
    if (req := data.get("requires_env")) is not None:
        if not isinstance(req, list):
            errs.append(f"{manifest_path}: requires_env must be a list of env var names")
        else:
            for name in req:
                if not isinstance(name, str) or not ENV_NAME_RE.match(name.strip()):
                    errs.append(
                        f"{manifest_path}: requires_env entry {name!r} is not an env var name "
                        f"(expected UPPER_SNAKE, e.g. CENSUS_API_KEY)"
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
