#!/usr/bin/env python3
"""Declare `data_period_field` on sources whose payload actually carries a period.

Change harden-crawl-delivery 3.3. The watermark (3.2) needs a field name per
source; guessing a universal key would silently misjudge 80+ sources. This
script only writes a declaration it has *verified against the source's own last
successful run*:

  1. candidate key exists and its values normalise (watermark state == ok)
  2. the source has a cadence (``data_period`` in day/month/year) — declaring a
     field on an ``irregular`` source would imply a stall judgement the cadence
     cannot support
  3. the key is a *data period*, not a publish timestamp or a forecast date

Rules 2 and 3 are why this is not a batch sed. Measured exclusions:

  - ``open-meteo`` / ``nasa-power``: ``date`` is a forecast day (future dates) —
    a watermark there would always look current
  - ``cdata.publish_date`` / ``miit-gxsj.publish_date`` / ``shclearing-*``:
    publish time, not the period the numbers describe
  - ``gdacs``: event date on an irregular event feed

Dry-run by default; ``--apply`` writes. Read-only over the DB.

Usage:
    python3 scripts/declare_data_period_field.py --dsn "$FD_CRAWL_DB_URL"
    python3 scripts/declare_data_period_field.py --dsn ... --apply
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from datetime import date  # noqa: E402

from fd_industry_data.health.watermark import judge, max_period  # noqa: E402

#: 这些键在其他源上是合法数据期，但在这些源上不是（见模块 docstring 的实测依据）。
DENY: dict[str, set[str]] = {
    "open-meteo": {"date"},      # 预报日，恒在未来
    "nasa-power": {"date"},      # 同上（含 forecast 参数）
    "gdacs": {"date"},           # 事件日，事件流无节奏
    "cisa": {"publish_date"},
    "miit-gxsj": {"publish_date"},
    "moa-variety-approval": {"publish_date"},
    "shclearing-capital-notes": {"publish_date"},
    "fangchan-market": {"publish_date"},
    "cpcaauto-monthly": {"publish_date"},
    "dounan-flower-price": {"publish_date"},
}

#: 语义上不是「数据期」的键：一律不声明，即使值可归一。
NEVER = {"publish_date", "scraped_at", "source_url", "url"}

CANDIDATE_KEYS = ("period", "month", "year", "trade_date", "price_date", "date")


def manifest_meta() -> dict[str, dict]:
    import yaml

    out: dict[str, dict] = {}
    for m in sorted((REPO / "spiders").glob("*/manifest.yaml")):
        try:
            data = yaml.safe_load(m.read_text()) or {}
        except yaml.YAMLError:
            continue
        out[m.parent.name] = {
            "path": m,
            "data_period": str(data.get("data_period") or "").strip().lower(),
            "declared": data.get("data_period_field"),
        }
    return out


def latest_items(dsn: str, binary: str = "psql") -> dict[str, list]:
    sql = """
    select cr.source, jsonb_agg(ci.payload)
    from crawl_items ci join crawl_runs cr on cr.id = ci.run_id
    where cr.id in (select max(id) from crawl_runs where status = 'success' group by source)
    group by cr.source
    """
    proc = subprocess.run([binary, dsn, "-At", "-F", "\t", "-c", sql],
                          capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()[:300]}")
    out: dict[str, list] = {}
    for line in proc.stdout.splitlines():
        if not line.strip() or "\t" not in line:
            continue
        source, raw = line.split("\t", 1)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        out[source.strip()] = payload if isinstance(payload, list) else []
    return out


def choose(source: str, meta: dict, items: list) -> tuple[str | None, str]:
    """(field, reason). field None means "leave undeclared"."""
    if meta.get("declared"):
        return None, "already declared"
    cadence = meta.get("data_period")
    if cadence not in ("day", "month", "year"):
        return None, f"data_period {cadence or '<missing>'} has no cadence"
    for key in CANDIDATE_KEYS:
        if key in NEVER or key in DENY.get(source, set()):
            continue
        wm = max_period(items, key)
        if wm["state"] != "ok":
            continue
        # 粒度比周期粗时判不出停滞（如 year 值 + month 周期），声明了也只会永远
        # 得到 unavailable —— 不如不声明，让它明确落在「水位不可得」。
        verdict = judge(wm, cadence, date.today())
        if verdict["state"] == "unavailable" and "coarser" in str(verdict.get("reason", "")):
            continue
        return key, f"verified on last run (granularity={wm['period'].granularity}, seen={wm['seen']})"
    return None, "no candidate key normalises on the last run"


def apply(meta: dict, field: str) -> bool:
    path: Path = meta["path"]
    text = path.read_text()
    if re.search(r"^data_period_field:", text, re.M):
        return False
    line = (f"data_period_field: {field}   # 水位字段（harden-crawl-delivery 3.3）："
            f"逐源声明并已在最近一次成功运行上验证可归一")
    m = re.search(r"^data_period\b.*$", text, re.M) or re.search(r"^name:.*$", text, re.M)
    if not m:
        return False
    path.write_text(text[:m.end()] + "\n" + line + text[m.end():])
    return True


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv[1:])

    metas = manifest_meta()
    items_by_source = latest_items(args.dsn)

    declared, skipped, missing = [], [], []
    for source, meta in sorted(metas.items()):
        items = items_by_source.get(source)
        if items is None:
            missing.append(source)
            continue
        field, reason = choose(source, meta, items)
        if field is None:
            skipped.append((source, reason))
            continue
        declared.append((source, field, reason))

    print(f"declare_data_period_field: {len(declared)} to declare, "
          f"{len(skipped)} skipped, {len(missing)} with no successful run")
    for source, field, reason in declared:
        print(f"  {'write' if args.apply else 'would write'} {source:<28} {field:<12} {reason}")
    for source, reason in skipped:
        if "already declared" not in reason:
            print(f"  skip  {source:<28} {reason}")

    if args.apply:
        written = sum(1 for source, field, _ in declared if apply(metas[source], field))
        print(f"written: {written}")
    else:
        print("(dry run; pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
