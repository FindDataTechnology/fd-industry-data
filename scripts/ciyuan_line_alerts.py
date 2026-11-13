#!/usr/bin/env python3
"""词元按线花费告警 + 分组额度实况（只读）。

这一条告警线回答两个问题，且**两个都必须答**，缺一个就会误导：

1. **有没有哪条线今天烧超了？** 阈值默认是「爆炸检测」而不是预算：按 2026-11-13
   实测基线（萬星 ≈$4.2/日、识律 ≈$2.1/日、openrouter ≈$11/日），默认 50 美元/日
   只会在真实爆炸时响。按线阈值可在 config 的 `thresholds.lines` 覆盖。
2. **这些线有没有额度拦着？** —— 2026-11-13 实测：**16 个分组、16 个三档额度全
   NULL**。也就是说没有一条线被任何上限拦过。这一项每次都会打印出来：只报花费
   不报「无上限」，读者会把「没有告警」当成「额度在管着」，那是最坏的误读。

数据来自 sub2api 库的只读查询（`sub2api` 仓 `tools/line-quota.sql`，输出一行 JSON）。
口径（线身份＝key 名首段、归一只认 `tools/line-aliases.json`）全在那个 SQL 里，
本文件不重复定义，也不自己拼 SQL。

退出码（与其它告警线同一套）：
  0 = 正常（含无告警）
  1 = 死信（应送达却 0 送达）
  2 = 脚本自身出错（数据形状不符）
  3 = 投递未配置（**不是成功**）
  4 = 读不到数据（监控失明）

用法：
  python3 scripts/ciyuan_line_alerts.py --from-json /tmp/quota.json --dry-run
  python3 scripts/ciyuan_line_alerts.py --dry-run          # 走 CIYUAN_DSN 真查
  python3 scripts/ciyuan_line_alerts.py --run              # 正式：写状态 + 发通知
  python3 scripts/ciyuan_line_alerts.py --require-limits   # 把「无上限」当失败（供闸用）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fd_industry_data.alerts_core import (  # noqa: E402
    RuleState, append_events, dispatch, evaluate_threshold, load_state, save_state,
)
from fd_industry_data.alerts_core.delivery import (  # noqa: E402
    DeliveryNotConfigured, DeliveryReceipt, judge, resolve_target, smtp_from_env,
    strict_enabled,
)
from fd_industry_data.alerts_core.state import (  # noqa: E402
    FIRING, INSUFFICIENT, OK, RESOLVED,
)

CONFIG = ROOT / "config" / "ciyuan-line-alerts.json"
REPORT_DIR = Path(os.environ.get("FD_CIYUAN_ALERT_REPORTS")
                  or (ROOT / "reports" / "ciyuan-line-alerts"))
STATE = REPORT_DIR / "state" / "alert-state.json"
EVENTS = REPORT_DIR / "state" / "events.jsonl"

#: SQL 真源在另一个仓：`sub2api/tools/line-quota.sql`。不复制一份到这里——
#: 复制就会漂，而这条告警的口径必须与报表逐字同源（见 ciyuan-line-metering spec）。
SQL_PATH = Path(os.environ.get("CIYUAN_LINE_QUOTA_SQL")
                or (ROOT.parent / "sub2api" / "tools" / "line-quota.sql"))

RULE_ID = "ciyuan-line-daily-spend"
EXIT_OK = 0
EXIT_DEAD_LETTER = 1
EXIT_SCRIPT_ERROR = 2
EXIT_NOT_CONFIGURED = 3
EXIT_BLIND = 4

#: 默认阈值：**爆炸检测**，不是预算。2026-11-13 基线总额 ≈$26/日（含 openrouter），
#: 单线最高 ≈$11/日。50 美元/日 = 单线 5 倍于当前最高基线。
DEFAULT_DAILY_USD = 50.0


# ─── 取数 ─────────────────────────────────────────────────────────────────────

def load_sql(days: int, path: Path = SQL_PATH) -> str:
    """读 SQL 并把 ``:days`` 换成整数字面量。

    SQL 刻意不用 psql 的 ``\\if``/``\\set`` 默认块：这个文件既要给 psql 跑（`-v days=7`），
    也要给 psycopg2 跑（runner 镜像里只有 psycopg2，没有 psql 客户端）。
    元命令只有 psql 认，所以默认值改由调用方给。
    """
    text = path.read_text(encoding="utf-8")
    if ":days" not in text:
        raise ValueError(f"{path} 里没有 :days 占位符——形状变了，先核对再改这里")
    return text.replace(":days", str(int(days)))


def fetch_json(dsn: str, days: int) -> dict:
    """只读查询，返回解析后的 JSON。驱动优先 psycopg3 → psycopg2。

    两条都没有时**明确报失明**，不回退去猜：告警线读不到数据必须 exit 4，
    绝不能落成「没有告警＝一切正常」（本仓反复栽过的假绿形态）。
    """
    sql = load_sql(days)
    try:
        import psycopg  # noqa: PLC0415
    except ImportError:
        psycopg = None  # type: ignore[assignment]
    if psycopg is not None:
        with psycopg.connect(dsn, connect_timeout=40) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
        return _parse(row)

    try:
        import psycopg2  # noqa: PLC0415
    except ImportError as exc:
        raise LookupError("既没有 psycopg 也没有 psycopg2；告警线不做 psql 文本兜底") from exc
    conn = psycopg2.connect(dsn, connect_timeout=40)
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
    finally:
        conn.close()
    return _parse(row)


def _parse(row) -> dict:
    if not row or not row[0]:
        raise ValueError("查询没有返回 JSON 行")
    raw = row[0]
    return json.loads(raw) if isinstance(raw, str) else raw


# ─── 判定 ─────────────────────────────────────────────────────────────────────

def line_daily_totals(doc: dict) -> dict[str, dict]:
    """把「线 × 日」压成「线 × 最新一日」+ 窗口合计。

    告警看的是**最近一天**：窗口合计在缓慢涨价时几乎不动，看不见"今天突然翻倍"。
    窗口合计仍然给出来，供邮件正文里做对照。
    """
    out: dict[str, dict] = {}
    for row in doc.get("per_line_day") or []:
        line = row.get("line") or "unknown"
        day = str(row.get("day"))
        usd = float(row.get("usd") or 0)
        req = int(row.get("requests") or 0)
        cur = out.setdefault(line, {"today_usd": 0.0, "today_requests": 0,
                                    "window_usd": 0.0, "latest_day": None,
                                    "raw_prefixes": []})
        cur["window_usd"] += usd
        if cur["latest_day"] is None or day > cur["latest_day"]:
            cur["latest_day"] = day
            cur["today_usd"] = usd
            cur["today_requests"] = req
        elif day == cur["latest_day"]:
            cur["today_usd"] += usd
            cur["today_requests"] += req
        if line == "unknown" and row.get("raw_prefix_if_unknown"):
            cur["raw_prefixes"].append(row["raw_prefix_if_unknown"])
    for value in out.values():
        value["raw_prefixes"] = sorted(set(value["raw_prefixes"]))[:5]
    return out


def thresholds_of(cfg: dict) -> tuple[float, dict[str, float]]:
    """读阈值。``_`` 开头的键是注记（本仓配置惯例：``_note`` / ``_why_*``），不当值。

    线名的拼写错误**不在这里静默掉**——不匹配实际线名的阈值会在 summary 里点名
    （见 ``unused_thresholds``）：一个拼错的线名等于那条线永远用默认阈值，
    而没有任何东西会红。
    """
    th = (cfg or {}).get("thresholds") or {}
    default = float(th.get("default_daily_usd") or DEFAULT_DAILY_USD)
    per_line: dict[str, float] = {}
    for key, value in (th.get("lines") or {}).items():
        if str(key).startswith("_"):
            continue
        per_line[str(key)] = float(value)
    return default, per_line


def evaluate(doc: dict, states: dict[str, RuleState], now: datetime,
             *, default_usd: float, per_line: dict[str, float]
             ) -> tuple[dict[str, RuleState], list[dict], list[dict]]:
    totals = line_daily_totals(doc)
    groups = doc.get("groups") or []
    unlimited = [g for g in groups if g.get("limits_unset") is True]

    new_states: dict[str, RuleState] = {k: RuleState(**v) if isinstance(v, dict) else v
                                        for k, v in states.items()}
    events: list[dict] = []
    detail: list[dict] = []

    for line, facts in sorted(totals.items()):
        limit = per_line.get(line, default_usd)
        key = f"{RULE_ID}:{line}"
        triggered = facts["today_usd"] >= limit
        new_states[key], ev, verdict = evaluate_threshold(
            new_states.get(key, RuleState()), triggered=triggered,
            key=key, at=now, value=round(facts["today_usd"], 2), threshold=limit,
            detail=f"{line} 当日 ${facts['today_usd']:.2f}（阈值 ${limit:.2f}）")
        events.extend(ev)
        detail.append({
            "rule_id": RULE_ID, "line": line, "verdict": verdict,
            "today_usd": round(facts["today_usd"], 2),
            "today_requests": facts["today_requests"],
            "window_usd": round(facts["window_usd"], 2),
            "threshold_usd": limit,
            "latest_day": facts["latest_day"],
            "raw_prefixes": facts["raw_prefixes"],
            # 这一行是「没有告警 ≠ 额度在管着」的证据，必须随每条明细带出去
            "unlimited_groups": len(unlimited),
            "groups_total": len(groups),
        })

    # 窗口内没有任何数据的线：如实记 INSUFFICIENT，不许当成 $0 通过
    for line in (doc.get("lines_expected") or []):
        if line not in totals:
            detail.append({"rule_id": RULE_ID, "line": line, "verdict": INSUFFICIENT,
                           "note": "窗口内无任何调用数据（不是 $0，是无数据）"})
    return new_states, events, detail


def compose_messages(events: list[dict], detail: list[dict], doc: dict) -> list[dict]:
    by_line = {d["line"]: d for d in detail if d.get("line")}
    unlimited = [g for g in (doc.get("groups") or []) if g.get("limits_unset") is True]
    no_limit_note = (
        f"⚠️ 本次查询的 {len(doc.get('groups') or [])} 个分组里有 {len(unlimited)} 个"
        f"三档额度全为空（无上限）——这些线的花费不会被任何配额拦住，"
        f"只能靠本告警在超阈时通知。"
    ) if unlimited else "所有分组都有配置额度。"

    msgs: list[dict] = []
    for e in events:
        line = e["key"].split(":", 1)[-1]
        d = by_line.get(line, {})
        resolved = e["kind"] == RESOLVED
        tag = "[词元线花费回落]" if resolved else "[词元线花费超阈]"
        subject = f"{tag} {line} 当日 ${d.get('today_usd')}（阈值 ${d.get('threshold_usd')}）"
        body = json.dumps({
            "line": line,
            "verdict": e["kind"],
            "today_usd": d.get("today_usd"),
            "today_requests": d.get("today_requests"),
            "window_usd": d.get("window_usd"),
            "latest_day": d.get("latest_day"),
            "threshold_usd": d.get("threshold_usd"),
            "raw_prefixes_if_unknown": d.get("raw_prefixes") or None,
            "unlimited_groups": len(unlimited),
            "groups_total": len(doc.get("groups") or []),
            "no_limit_note": no_limit_note,
            "hint": ("先看该线今天的 key 与模型分布（sub2api 仓 tools/line-metrics.sql）；"
                     "若属预期增长，把 config 的 thresholds.lines.<线> 调高并留下理由——"
                     "不要默默把阈值抬到不响。"),
            "at": e.get("at"),
        }, ensure_ascii=False, indent=2)
        msgs.append({"rule_id": e.get("rule_id", RULE_ID), "subject": subject, "body": body})
    return msgs


# ─── 入口 ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--from-json", help="离线数据（line-quota.sql 的输出）；缺省走 CIYUAN_DSN 真查")
    ap.add_argument("--days", type=int, default=7, help="窗口天数（默认 7）")
    ap.add_argument("--dry-run", action="store_true", help="只判定不发送、不写状态")
    ap.add_argument("--run", action="store_true", help="正式跑：写状态/留痕/发通知")
    ap.add_argument("--require-limits", action="store_true",
                    help="把「存在无上限分组」当失败（退出 1）。默认只报事实——"
                         "16/16 全无上限是配置现状，不该让闸常年红")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)

    if args.from_json:
        try:
            doc = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"error": f"--from-json 读不了：{exc}"}, ensure_ascii=False))
            return EXIT_SCRIPT_ERROR
    else:
        dsn = os.environ.get("CIYUAN_DSN") or os.environ.get("SUB2API_DSN")
        if not dsn:
            print(json.dumps({"error": "需要 CIYUAN_DSN（或 --from-json）",
                              "blind": True}, ensure_ascii=False))
            return EXIT_BLIND
        try:
            doc = fetch_json(dsn, args.days)
        except LookupError as exc:
            print(json.dumps({"error": str(exc), "blind": True}, ensure_ascii=False))
            return EXIT_BLIND
        except Exception as exc:  # noqa: BLE001 - 连不上/形状不符都算失明
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}", "blind": True},
                             ensure_ascii=False))
            return EXIT_BLIND

    if not isinstance(doc, dict) or "per_line_day" not in doc:
        print(json.dumps({"error": "数据形状不符：缺 per_line_day", "blind": True},
                         ensure_ascii=False))
        return EXIT_SCRIPT_ERROR

    try:
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # 缺声明＝这条告警线不成立，必须非零退出并点名缺什么（alert-delivery 的硬要求）
        print(json.dumps({"error": f"投递声明缺失或不可读（{args.config}）：{exc}"},
                         ensure_ascii=False))
        return EXIT_NOT_CONFIGURED

    default_usd, per_line = thresholds_of(cfg)
    states = load_state(STATE)
    new_states, events, detail = evaluate(doc, states, now,
                                          default_usd=default_usd, per_line=per_line)
    msgs = compose_messages(events, detail, doc)
    firing = sum(1 for e in events if e["kind"] == FIRING)

    unlimited = [g["group"] for g in (doc.get("groups") or []) if g.get("limits_unset") is True]
    groups_total = len(doc.get("groups") or [])

    receipt = DeliveryReceipt()
    verdict_code = None
    if args.run and msgs:
        try:
            target = resolve_target(cfg, os.environ)
            cfg_smtp = smtp_from_env(os.environ, recipients=target.to)
        except (OSError, ValueError, DeliveryNotConfigured) as exc:
            receipt.reasons.append(str(exc))
            verdict_code = judge(receipt, firing=firing, strict=strict_enabled(os.environ),
                                 not_configured_reason=str(exc))
        else:
            sent = dispatch(msgs, cfg_smtp)
            receipt = DeliveryReceipt(sent=sent.get("sent", 0), failed=sent.get("failed", 0),
                                      skipped=1 if sent.get("skipped") else 0,
                                      reasons=[str(f) for f in sent.get("failures", [])])
            verdict_code = judge(
                receipt, firing=firing, strict=strict_enabled(os.environ),
                not_configured_reason="SMTP 凭据缺失（host/from）" if receipt.skipped else None)

    if args.run:
        # **只在「该发的都发出去了」时才推进状态。**
        # 2026-11-13 踩到：投递未配置的那一轮也写了状态，于是 SMTP 补好之后
        # 这条线已经算「报过了」，要等它回落再重新触发才会补发——告警被静默吞掉。
        # 同理 failed>0 也不推进：下一轮该重试。
        delivered = (not msgs) or receipt.sent >= len(msgs)
        if delivered:
            append_events(EVENTS, events)
            save_state(STATE, {k: v.to_dict() for k, v in new_states.items()})
        else:
            print(json.dumps({
                "warn": "投递未完成，本轮不推进状态（下一轮会重试同一批告警）",
                "messages": len(msgs), "sent": receipt.sent, "failed": receipt.failed,
                "skipped": receipt.skipped,
            }, ensure_ascii=False), file=sys.stderr)

    summary = {
        "blind": False,
        "window_days": doc.get("window_days"),
        "lines": len(detail),
        "firing": firing,
        "events": len(events),
        "messages": len(msgs),
        # 这一项就是 3.2 的判据：永远打印，永不静默
        "groups_total": groups_total,
        "groups_without_limits": unlimited,
        "no_limit_note": (f"{len(unlimited)}/{groups_total} 个分组三档额度全为空 ⇒ 无上限；"
                          f"没有告警不等于额度在管着") if unlimited else "所有分组都有额度",
        # 配了阈值但数据里没有这条线 ⇒ 多半是线名拼错（阈值静默失效）
        "unused_thresholds": sorted(set(per_line) - {d["line"] for d in detail if d.get("line")}),
        "threshold_default_usd": default_usd,
        "delivery": receipt.as_dict(),
        "verdict": verdict_code.as_dict() if verdict_code else None,
        "detail": detail,
    }
    print(json.dumps(summary, ensure_ascii=False))

    if verdict_code is not None:
        return verdict_code.exit_code
    if args.require_limits and unlimited:
        # 只在显式要求时把「无上限」当失败：让闸能证明自己会红，
        # 又不把常态配置变成一条常年红的线。
        return EXIT_DEAD_LETTER
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
