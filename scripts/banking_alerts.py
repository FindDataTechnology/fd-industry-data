#!/usr/bin/env python
"""银行数据源三层预警（add-banking-source-alerting）。

三层互不触发：
  · **健康级** —— 源是否还在出数（复用 ``fd_industry_data.health.triage.classify``）
  · **阈值级** —— 监管指标是否越过官方监管线（数值全在 config，代码无硬编码）
  · **事件级** —— 定期报告到期未披露 / 新增行政处罚

**为什么复用 health 而不是再造**：``fd_industry_data/health/`` 是各线唯一真正共享的部分
（triage/inspector/ticket）；而告警引擎已有农业/花卉/餐饮三份分叉副本，本节改用
``fd_industry_data.alerts_core`` 公共内核，不再复制第四份。

诚实性纪律（都会静默出错，故逐条落测）：
  · 覆盖不足显式声明：比率指标只有 5 家，输出 ``coverage{covered, universe}``，
    其余记 ``not_covered``——**不写「正常」**；
  · 无观测 ≠ 达标：不进越阈分母；
  · 判据缺失 ≠ 恢复：缺数判 ``insufficient_data``，不发 resolved；
  · 源不健康：通知前置「信息可能不完整」，阈值 firing 降级为 ``pending``；
  · 事件无真实样本时如实声明「暂无真实触发样本」，不构造样本充数。

用法：
  python3 scripts/banking_alerts.py --dry-run
  python3 scripts/banking_alerts.py --disclosure-json out/d.json --penalty-json out/p.json \
      --today 2026-11-08 --dry-run
  python3 scripts/banking_alerts.py --run          # 读中央库 + 写状态/留痕/报告

退出码：0=已执行（无论是否告警）；1=有通知发送失败；2=脚本自身出错；
3=环境不满足（只读账号缺 semantic_observations 的 SELECT 权限）——3 不判故障但必须写明缺啥。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fd_industry_data.alerts_core import (  # noqa: E402
    RuleState, append_events, compare, dispatch, evaluate_event, evaluate_threshold,
    load_state, save_state, smtp_config_from_env,
)
from fd_industry_data.alerts_core.notify import SmtpConfig  # noqa: E402
from fd_industry_data.alerts_core.state import (  # noqa: E402
    FIRING, INSUFFICIENT, OK, PENDING, RESOLVED, attach_rule,
)
from fd_industry_data.health.triage import Run, classify  # noqa: E402

CONFIG = ROOT / "config" / "banking-alerts.json"
REPORT_DIR = ROOT / "reports" / "banking-alerts"
STATE = REPORT_DIR / "state" / "alert-state.json"
EVENTS = REPORT_DIR / "state" / "events.jsonl"

#: 日频源的期望周期（小时），与 manifest 的 cron 一致。
DAILY_PERIOD_HOURS = 24.0


# ─── 配置 ─────────────────────────────────────────────────────────────────────

def load_config(path: str | Path | None = None) -> dict:
    """读配置并硬校验「阈值出处」纪律。

    核不到出处的阈值**不允许启用**。放在加载期硬失败，是因为运行期只会静默少报/误报。
    """
    p = Path(path) if path else CONFIG
    cfg = json.loads(p.read_text(encoding="utf-8"))
    for r in cfg.get("threshold_rules", []):
        if r.get("enabled"):
            if not r.get("citation"):
                raise ValueError(f"{r['rule_id']} 启用但无 citation")
            if r.get("citation_verified") is False:
                raise ValueError(f"{r['rule_id']} 启用但 citation_verified=false（出处未核不得启用）")
        elif not r.get("blocked_reason"):
            raise ValueError(f"{r['rule_id']} 停用但没写 blocked_reason")
    if "不良率" in {r.get("indicator_zh") for r in cfg.get("threshold_rules", [])}:
        raise ValueError("不良率不得设阈值（监管无统一硬上限，只作事件级关注）")
    return cfg


# ─── 取数 ─────────────────────────────────────────────────────────────────────

def fetch_ratio_series(dsn: str, concepts: list[str]) -> dict:
    """取比率指标近期序列：``{(concept, entity_code): {"name", "points":[(date, value)]}}``。

    值在库里是 varchar，转不动 float 的行直接跳过——宁可少一个样本，
    也不要把坏值当 0 喂给阈值判定。
    """
    import psycopg2

    out: dict = {}
    with psycopg2.connect(dsn, connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute(
            "select c.code, e.code, e.name_zh, o.date, o.value "
            "from semantic_observations o "
            "join concepts c on c.id = o.concept_id "
            "join entities e on e.id = o.entity_id "
            "where c.code = any(%s) and e.entity_type = 'stock' "
            "order by c.code, e.code, o.date", (concepts,))
        for concept, entity, name, dt, raw in cur.fetchall():
            try:
                val = float(str(raw).replace(",", ""))
            except (TypeError, ValueError):
                continue
            slot = out.setdefault((concept, entity), {"name": name or entity, "points": []})
            slot["points"].append((str(dt)[:10], val))
    return out


def fetch_source_runs(dsn: str, sources: list[str]) -> dict[str, list[Run]]:
    import psycopg2

    out: dict[str, list[Run]] = {s: [] for s in sources}
    with psycopg2.connect(dsn, connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute(
            "select source, started_at, status, rows_written, error_head from crawl_runs "
            "where source = any(%s) order by started_at desc limit 200", (sources,))
        for source, started, status, rows, err in cur.fetchall():
            out.setdefault(source, []).append(Run(
                started_at=started.isoformat() if hasattr(started, "isoformat") else str(started),
                status="success" if str(status) in ("success", "ok", "succeeded") else "failed",
                rows=rows, error=err or ""))
    return out


def fetch_crawl_items(dsn: str, source: str) -> list[dict]:
    import psycopg2

    with psycopg2.connect(dsn, connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute(
            "select i.payload from crawl_items i join crawl_runs r on r.id = i.run_id "
            "where r.source = %s order by r.started_at desc limit 20000", (source,))
        rows = [r[0] for r in cur.fetchall()]
    return [json.loads(r) if isinstance(r, str) else r for r in rows]


# ─── 健康级 ───────────────────────────────────────────────────────────────────

@dataclass
class SourceHealth:
    source: str
    healthy: bool
    category: str | None
    detail: str


def judge_health(source: str, runs: list[Run], now: datetime) -> SourceHealth:
    """复用共享分诊器。没有任何运行记录 → ``scheduled_absent``（调度面故障，不是健康）。"""
    verdict = classify(source, unit=f"{source}-unit", expected_period_hours=DAILY_PERIOD_HOURS,
                       runs=runs, now=now, scheduled_absent=not runs)
    healthy = verdict.category is None and not verdict.needs_human
    return SourceHealth(source=source, healthy=healthy, category=verdict.category,
                        detail=verdict.rule_trace or verdict.suggested_action)


# ─── 判定 ─────────────────────────────────────────────────────────────────────

def threshold_verdicts(cfg: dict, series: dict, states: dict, *, now: datetime,
                       unhealthy_sources: set[str] | None = None) -> tuple[list[dict], dict, dict]:
    """阈值级判定。

    ``unhealthy_sources``：不健康的源集合。降级**按规则各自的 depends_on 判定**——
    早期版本用「任一源不健康即整体降级」，结果事件源从未排期（health=contract）时
    把本来健康的阈值层一起打成 pending，等于永远不告警。
    """
    unhealthy_sources = unhealthy_sources or set()
    verdicts: list[dict] = []
    new_states = dict(states)
    covered: set[str] = set()
    mapping = cfg.get("indicator_concepts") or {}

    for rule in cfg.get("threshold_rules", []):
        if not rule.get("enabled"):
            continue
        degraded = rule.get("depends_on") in unhealthy_sources
        concept = mapping.get(rule["indicator_zh"])
        if not concept:
            raise ValueError(f"{rule['rule_id']} 无 indicator_concepts 映射")
        for (c_code, entity), slot in series.items():
            if c_code != concept:
                continue
            covered.add(entity)
            if not slot["points"]:
                continue
            dt, val = slot["points"][-1]
            state_key = f"{rule['rule_id']}:{entity}"
            prev = RuleState.from_dict(states.get(state_key))
            # operator/value 写的是**监管要求**（如「资本充足率 >= 10.5」），
            # 告警条件是这个要求被违反。方向属于业务语义，故在这里算，不塞进内核。
            satisfied = compare(val, rule["operator"], rule["value"])
            new, events, verdict = evaluate_threshold(
                prev, triggered=not satisfied, key=dt, at=now, value=val,
                threshold=rule["value"],
                detail=f"{val} {'满足' if satisfied else '未满足'} "
                       f"{rule['operator']} {rule['value']}")
            if degraded and verdict == FIRING:
                # 源不健康：不擅自 resolved，也不新发 firing —— 记 pending 等源恢复再判
                verdict, events, new = PENDING, [], prev
            new_states[state_key] = new.to_dict()
            verdicts.append({
                "rule_id": rule["rule_id"], "indicator_zh": rule["indicator_zh"],
                "entity": entity, "name": slot["name"], "key": dt, "value": val,
                "operator": rule["operator"], "threshold": rule["value"],
                "citation": rule.get("citation"),
                "citation_version": rule.get("citation_version"),
                "verdict": verdict,
                "events": attach_rule(events, state_key),
            })
    return verdicts, new_states, {"covered": sorted(covered)}


def disclosure_verdicts(rows: list[dict], states: dict, *, today: date,
                        now: datetime) -> tuple[list[dict], dict, dict]:
    """披露逾期：每个 (代码, 报告类型, 年度) 独立一个小状态机。

    ``value`` 取 1（逾期）/0（已披露），键固定为该报告期 —— 于是同一报告期只报一次，
    补披露后发 resolved。
    """
    verdicts: list[dict] = []
    new_states = dict(states)
    counts = {"total": 0, "overdue": 0, "published": 0, "no_due_date": 0}
    for row in rows:
        code = str(row.get("stock_code") or "")
        rtype, fy = row.get("report_type"), row.get("fiscal_year")
        due = row.get("change_date") or row.get("first_appoint_date")
        published = row.get("actual_publish_date")
        counts["total"] += 1
        if not due:
            counts["no_due_date"] += 1
            continue
        key = f"{code}:{rtype}:{fy}"
        overdue = 1 if (published is None and str(due) < today.isoformat()) else 0
        if overdue:
            counts["overdue"] += 1
        else:
            counts["published"] += 1
        state_key = f"bank-disclosure-overdue:{key}"
        prev = RuleState.from_dict(states.get(state_key))
        new, events, verdict = evaluate_threshold(
            prev, triggered=bool(overdue), key=key, at=now, value=overdue, threshold=1,
            detail=f"{due} 预约、实际披露 {'无' if published is None else published}")
        new_states[state_key] = new.to_dict()
        verdicts.append({
            "rule_id": "bank-disclosure-overdue", "key": key, "stock_code": code,
            "stock_name": row.get("stock_name"), "report_type": rtype, "fiscal_year": fy,
            "due": due, "published": published, "verdict": verdict,
            "events": attach_rule(events, state_key),
        })
    return verdicts, new_states, counts


def penalty_verdicts(rows: list[dict], states: dict, *, now: datetime,
                     aliases: dict | None = None,
                     index: dict[str, str | None] | None = None
                     ) -> tuple[list[dict], dict, dict]:
    """处罚增量：按 ``doc_id`` 取增量，主体匹配不上标 ``unattributed``（不强归属）。"""
    aliases = aliases or {}
    keys = [str(r["doc_id"]) for r in rows if r.get("doc_id")]
    prev = RuleState.from_dict(states.get("bank-penalty-new"))
    new, events, _ = evaluate_event(prev, new_keys=keys, at=now)
    new_states = dict(states)
    new_states["bank-penalty-new"] = new.to_dict()
    # 一份文书常含多个主体（实测 40 行只对应 15 个 doc_id），故按 doc_id 分组，
    # 事件仍按文书去重，但把该文书下的**所有**主体都带出来，否则归属信息会丢。
    by_doc: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("doc_id"):
            by_doc.setdefault(str(r["doc_id"]), []).append(r)
    out: list[dict] = []
    for e in attach_rule(events, "bank-penalty-new"):
        group = by_doc.get(e["key"], [])
        first = group[0] if group else {}
        out.append({
            "rule_id": "bank-penalty-new", "key": e["key"],
            "office": first.get("office"),
            "authority_level": first.get("authority_level"),
            "penalty_no": first.get("penalty_no"),
            "doc_date": first.get("doc_date"),
            "content": (first.get("penalty_content") or "")[:200],
            "parties": [{"party": g.get("party"),
                         "attribution": attribute(g.get("party"), aliases, index)}
                        for g in group],
            "verdict": FIRING,
            "events": [e],
        })
    return out, new_states, {"total": len(rows), "documents": len(by_doc),
                             "new": len(out)}


#: 非银行机构的粗分特征。处罚源是全机构口径（总局/省局/分局），实测 40 行里混着
#: 保险主体与自然人（「民生人寿…及相关责任人」「原交通银行…员工仇征」），
#: 故先分机构类型，再谈归属——个股员工不是银行的处罚。
_NON_BANK = ("人寿", "财险", "保险", "再保险", "资产管理", "信托", "租赁", "消费金融",
             "汽车金融", "财务有限公司", "理财")
#: 自然人处罚的特征词。**必须单独判**：真实数据里有「原交通银行股份有限公司员工仇征」，
#: 归一后是「交通银行」，不拦就会把个人的处罚记到银行头上——错归属比不归属更坏。
_INDIVIDUAL = ("员工", "个人", "从业人员", "董事", "监事", "高级管理人员", "客户经理")


def classify_institution(party: str | None) -> str:
    name = (party or "").strip()
    if not name:
        return "unknown"
    if any(k in name for k in _INDIVIDUAL):
        return "individual"
    if "银行" in name:
        return "bank"
    if any(k in name for k in _NON_BANK):
        return "non-bank"
    return "unknown"


_SUFFIXES = ("股份有限公司", "有限责任公司", "有限公司", "及相关责任人", "（", "(")
_PREFIXES = ("中国", "原", "中国银行保险监督管理委员会")


def normalize_party(name: str) -> str:
    """机构名归一，供全称/简称两种写法对齐。

    步骤固定、顺序敏感（踩过：先切「及相关责任人」就不再切「股份有限公司」，
    归一结果停在「光大银行股份有限公司」，永远匹配不上）：

    1. 去掉「（…」「(…」附加语；
    2. 去掉尾部「及相关责任人」；
    3. 从**最早出现**的公司形式后缀处截断（股份有限公司 / 有限责任公司 / 有限公司）；
    4. 去「中国」前缀，但要求截断后仍 ≥6 字——否则「中国银行」会被削成「银行」，
       匹配上所有银行；
    5. 去「原」前缀（处罚原文常写「原XX银行…员工」），同法保留最短长度。
    """
    s = (name or "").strip()
    for cut in ("（", "("):
        idx = s.find(cut)
        if idx > 0:
            s = s[:idx]
    if s.endswith("及相关责任人"):
        s = s[: -len("及相关责任人")]
    positions = [s.find(cut) for cut in ("股份有限公司", "有限责任公司", "有限公司")]
    positions = [i for i in positions if i > 0]
    if positions:
        s = s[: min(positions)]
    if s.startswith("中国") and len(s) - 2 >= 4:
        s = s[2:]
    elif s.startswith("原") and len(s) - 1 >= 4:
        s = s[1:]
    return s.strip()


def build_alias_index(aliases: dict[str, str]) -> dict[str, str | None]:
    """构造 归一名 → 代码 的索引；一个归一名对上多个代码时置 None（歧义即不归）。"""
    idx: dict[str, str | None] = {}
    for full, code in (aliases or {}).items():
        if str(full).startswith("_"):
            continue
        for form in {full, normalize_party(full)}:
            if len(form) < 4:
                continue
            if form in idx and idx[form] != code:
                idx[form] = None      # 歧义
            else:
                idx[form] = code
    return idx


def attribute(party: str | None, aliases: dict[str, str],
              index: dict[str, str | None] | None = None) -> dict:
    """机构名 → 监控主体。

    顺序：空 → 非银行机构 → 别名精确匹配 → 否则 ``unattributed``。
    **错归属比不归属更坏**：名不匹配就保留原名，绝不按相似度硬猜。
    """
    name = (party or "").strip()
    if not name:
        return {"status": "unattributed", "reason": "empty-party", "raw": party}
    kind = classify_institution(name)
    if kind == "individual":
        return {"status": "unattributed", "reason": "individual", "raw": name}
    if kind == "non-bank":
        return {"status": "unattributed", "reason": "not-a-bank", "raw": name}
    if name in aliases and not str(name).startswith("_"):
        return {"status": "matched", "code": aliases[name], "method": "exact", "raw": name}
    idx = index if index is not None else build_alias_index(aliases)
    norm = normalize_party(name)
    hit = idx.get(norm, "MISS")
    if hit == "MISS" or hit is None:
        return {"status": "unattributed",
                "reason": "ambiguous" if hit is None else "no-match",
                "normalized": norm, "raw": name, "institution_type": kind}
    return {"status": "matched", "code": hit, "method": "normalized",
            "normalized": norm, "raw": name}


# ─── 输出 ─────────────────────────────────────────────────────────────────────

def compose_messages(verdicts: list[dict], *, unhealthy: list[SourceHealth]) -> list[dict]:
    """把需要通知的判定拼成邮件。源不健康时正文前置「信息可能不完整」。"""
    prefix = ""
    if unhealthy:
        names = "、".join(h.source for h in unhealthy)
        prefix = f"【信息可能不完整】以下数据源当前不健康：{names}。\n\n"
    msgs: list[dict] = []
    for v in verdicts:
        if not v.get("events"):
            continue
        for e in v["events"]:
            if e["kind"] == RESOLVED:
                subject = f"[银行预警恢复] {v.get('indicator_zh') or v['rule_id']} {v.get('name') or v.get('key')}"
            else:
                subject = f"[银行预警] {v.get('indicator_zh') or v['rule_id']} {v.get('name') or v.get('key')}"
            body = prefix + json.dumps(
                {k: v[k] for k in v if k != "events"}, ensure_ascii=False, indent=2)
            msgs.append({"rule_id": v["rule_id"], "subject": subject, "body": body})
    return msgs


def coverage_block(cfg: dict, covered: list[str], universe_size: int) -> dict:
    universe = cfg.get("monitored_universe") or {}
    size = int(universe.get("size") or universe_size)
    return {"covered": len(covered), "universe": size,
            "covered_entities": covered,
            "note": "未覆盖主体在该指标上记 not_covered，既不进越阈分子也不计入达标"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--dry-run", action="store_true", help="不写状态、不发通知")
    ap.add_argument("--run", action="store_true", help="正式跑：写状态/留痕/报告")
    ap.add_argument("--today", help="判定基准日 YYYY-MM-DD（默认今天，便于复算）")
    ap.add_argument("--disclosure-json", help="披露日历行（spider 输出）；缺省读中央库")
    ap.add_argument("--penalty-json", help="处罚行（spider 输出）；缺省读中央库")
    ap.add_argument("--dsn-env", default="FD_CENTRAL_PG_DSN")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    now = datetime.now(timezone.utc)
    today = date.fromisoformat(args.today) if args.today else now.date()

    dsn = os.environ.get(args.dsn_env)
    # 演练要回答「**现在**真跑会发生什么」，所以照样读状态，只是不写回。
    # 早先写的是 `{} if dry_run else load_state(...)`，于是演练每次都把历史事件
    # 重新算成「新增 39 条」，与真实运行（0 条）不符——这种偏离正是演练失去意义的方式。
    states = load_state(STATE)

    # 健康级
    health: list[SourceHealth] = []
    if dsn:
        runs = fetch_source_runs(dsn, cfg["health"]["event_sources"])
        health = [judge_health(s, r, now) for s, r in runs.items()]
    unhealthy = [h for h in health if not h.healthy]

    # 事件级数据
    if args.disclosure_json:
        disclosure = json.loads(Path(args.disclosure_json).read_text(encoding="utf-8"))
    elif dsn:
        disclosure = fetch_crawl_items(dsn, "eastmoney-disclosure-plan")
    else:
        disclosure = []
    if args.penalty_json:
        penalty = json.loads(Path(args.penalty_json).read_text(encoding="utf-8"))
    elif dsn:
        penalty = fetch_crawl_items(dsn, "nfra-penalty")
    else:
        penalty = []

    # 阈值级数据
    concepts = [c for c in (cfg.get("indicator_concepts") or {}).values()]
    try:
        series = fetch_ratio_series(dsn, concepts) if dsn else {}
    except Exception as exc:  # noqa: BLE001
        # 只读账号常见缺 semantic_observations 的 SELECT（花卉线 F17b 同款）。
        # 这不是崩溃，是环境不满足——判红会让流水线长期飘红而失去信号。
        if "permission denied" in str(exc).lower() or "InsufficientPrivilege" in type(exc).__name__:
            print("环境不满足：只读账号缺 semantic_observations 的 SELECT 权限。")
            print("需要 DBA 执行：GRANT SELECT ON public.semantic_observations TO <健康巡检只读账号>;")
            return 3
        raise

    d_verdicts, states, d_counts = disclosure_verdicts(disclosure, states, today=today, now=now)
    p_verdicts, states, p_counts = penalty_verdicts(
        penalty, states, now=now, aliases=cfg.get("party_aliases"))
    t_verdicts, states, cov = threshold_verdicts(
        cfg, series, states, now=now,
        unhealthy_sources={h.source for h in unhealthy})

    all_verdicts = t_verdicts + d_verdicts + p_verdicts
    events = [e for v in all_verdicts for e in v.get("events") or []]
    msgs = compose_messages(all_verdicts, unhealthy=unhealthy)

    print(f"健康级: {[(h.source, 'ok' if h.healthy else h.category) for h in health] or '无遥测'}")
    print(f"阈值级: {len(t_verdicts)} 条判定 | 覆盖 {cov['covered']} 家"
          f" / 宇宙 {(cfg.get('monitored_universe') or {}).get('size')}")
    print(f"事件级: 披露 {d_counts} | 处罚 {p_counts}")
    print(f"事件 {len(events)} 条 | 待发通知 {len(msgs)} 条")

    if args.dry_run:
        for v in all_verdicts:
            if v.get("verdict") in (FIRING, RESOLVED, PENDING):
                print("  -", v["rule_id"], v.get("entity") or v.get("key"), v["verdict"],
                      v.get("value"), v.get("threshold"))
        print("（dry-run：未写状态/留痕/通知）")
        return 0

    written = append_events(EVENTS, events)
    save_state(STATE, states)

    # 收件人：env（FD_ALERT_SMTP_TO）优先，否则用配置里的名单。
    # **必须在构造 SmtpConfig 之前注入**：smtp_config_from_env() 缺收件人会直接返回
    # None，事后再改 cfg.recipients 是徒劳——踩过，配置里的收件人在流水线里形同虚设。
    # 收件人是配置不是机密（可版本化），凭据仍只走 env/Secrets。
    smtp_env = dict(os.environ)
    configured_to = (cfg.get("notify") or {}).get("recipients") or []
    if configured_to and not smtp_env.get("FD_ALERT_SMTP_TO"):
        smtp_env["FD_ALERT_SMTP_TO"] = ",".join(configured_to)
    cfg_smtp: SmtpConfig | None = smtp_config_from_env(smtp_env)
    result = dispatch(msgs, cfg_smtp)
    if result.get("failed"):
        append_events(EVENTS, [{"rule_id": f["rule_id"], "kind": "notify_failed",
                                "key": now.isoformat(), "at": now.isoformat(),
                                "detail": f["error"]} for f in result["failures"]])

    stamp = today.isoformat()
    outdir = REPORT_DIR / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": now.isoformat(), "today": stamp,
        "health": [{"source": h.source, "healthy": h.healthy, "category": h.category,
                    "detail": h.detail} for h in health],
        "coverage": coverage_block(cfg, cov["covered"], 0),
        "thresholds": t_verdicts, "disclosure": d_verdicts, "penalty": p_verdicts,
        "counts": {"disclosure": d_counts, "penalty": p_counts,
                   "events_written": written, "notify": result},
        "disabled_rules": [{"rule_id": r["rule_id"], "reason": r.get("blocked_reason")}
                           for r in cfg.get("threshold_rules", []) if not r.get("enabled")],
    }
    (outdir / "rules-banking.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"留痕 +{written} | 通知 sent={result['sent']} failed={result['failed']} "
          f"skipped={result.get('skipped')} | 报告 {outdir}")
    return 1 if result.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
