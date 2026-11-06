#!/usr/bin/env python3
"""存量悬置单元机器预分类（add-fleet-lifecycle-adjudication task 4.1）。

对三类存量悬置单元逐源生成裁决卷宗并写入裁决队列（status=escalated，
``origin=stock-preclassify``，区别于工单升级通道）：

1. 全部静默单元：spiders/ 下有 manifest 且 manifest 无非空 schedule；
2. L3 判定 unreachable 的在役单元：output/l3-reanalysis/registry.json
   verdict=unreachable 且单元目录仍在 spiders/（已归档的不属在役，跳过）；
3. 在档 manual 终态工单但未达升级阈值的源（drill-* 演练夹具跳过）。

去重并集后，已有卷宗/队列条目的源（flower-auction/inmetals/metal-com 工单
升级通道）跳过不冲突。

预分类口径（保守：不确定→park，不建议 retire）：
- 建议 retire：L3 unreachable 且域名实测死（NXDOMAIN/域名出售）或源方政策
  停爬——参照 archive/triage-cleanup-20261004|20261013 退役先例；
- 建议 park：unreachable 原因可能是临时网络/需浏览器（needs_browser/degraded）
  ——保留代码等出口；
- 建议 repair：曾产出过 golden 且（L3 verified 或有 L3 判定之后的在产实证）
  但近期静默的；
- 其余→建议 park + 说明。

只写 output/adjudication/cases/<src>.md 与 output/adjudication/queue.json；
不动 ledger（裁决仍由人工经 scripts/adjudicate.py decide 执行）。幂等：重复
执行时既有 stock-preclassify 条目刷新、escalated_at 不漂移；工单升级条目不动。

纯 stdlib + yaml；无网络、无随机。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health import adjudication  # noqa: E402

ORIGIN = "stock-preclassify"

L3_REGISTRY = REPO / "output/l3-reanalysis/registry.json"
# 证据指针（卷宗内用工作区相对路径书写，便于复核人点击/检索）
REF_L3_REGISTRY = "output/l3-reanalysis/registry.json (units[\"spiders/<src>\"])"
REF_L3_REPORT = "output/l3-reanalysis/reanalysis_report.md (2026-09-12 横截面)"
REF_TRIAGE_20260912 = "archive/triage-cleanup-20260912/INDEX.md"
REF_TRIAGE_20261004 = "archive/triage-cleanup-20261004/INDEX.md"
REF_TRIAGE_20261013 = "archive/triage-cleanup-20261013/INDEX.md"
REF_RECON = "data-source-scouting/INDUSTRY-DATA-SOURCES.md (finddata 工作区)"
REF_TICKETS = "reports/health-tickets/"

# 演练夹具：manual 工单属演练产物，不属存量悬置
DRILL_PREFIX = "drill-"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 悬置清单计算 ──────────────────────────────────────────────────────────

def silent_units(repo: Path) -> list[str]:
    """spiders/ 下有 manifest 且 manifest 无非空 schedule 的单元（升序）。"""
    spiders = Path(repo) / "spiders"
    out = []
    for d in sorted(spiders.iterdir()):
        m = d / "manifest.yaml"
        if not d.is_dir() or not m.is_file():
            continue
        try:
            doc = yaml.safe_load(m.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            doc = {}
        if not doc.get("schedule"):
            out.append(d.name)
    return out


def l3_unreachable_in_service(repo: Path) -> tuple[list[str], dict]:
    """L3 registry verdict=unreachable 且单元目录仍在 spiders/ 的单元。"""
    doc = json.loads(L3_REGISTRY.read_text(encoding="utf-8"))
    units = doc.get("units", {})
    in_service = []
    for key, u in sorted(units.items()):
        if u.get("verdict") != "unreachable" or not key.startswith("spiders/"):
            continue
        name = key.split("/", 1)[1]
        if (Path(repo) / "spiders" / name).is_dir():
            in_service.append(name)
    return in_service, units


def manual_below_threshold(repo: Path, tickets_dir: Path,
                           threshold: int) -> list[str]:
    """有 manual 终态工单但未达升级阈值的源（排除演练夹具）。"""
    counts = adjudication.manual_counts(tickets_dir)
    return sorted(
        src for src, n in counts.items()
        if 0 < n < threshold and not src.startswith(DRILL_PREFIX)
    )


# ── 逐源预分类（保守口径，全部带证据指针） ────────────────────────────────
# rationale：机器建议的理由；extra_evidence：追加证据指针（除 L3/manifest 外）。
# 同域前身/同形拷贝先例（archive/triage-cleanup-20260912/ 批量退役）：
PRED_FETCH = "同域前身已退役（archive/triage-cleanup-20260912/spiders/{pred}）——本单元为第二代拷贝"

SUGGESTIONS: dict[str, dict] = {
    # —— 静默 × L3 verified_html 200（站点活、单元静默、无 golden）→ park ——
    "agri-info": {"s": "park", "r": "L3 verified_html(200) 站点活但单元静默无 schedule；无 golden 样本，价值待评估；不确定→park",
                  "e": [f"{REF_RECON}:L24（已接入源清单）、:L477（农口既有覆盖已够，agri-info 为在产覆盖之一）"]},
    "caa": {"s": "park", "r": "L3 verified_html(200) 站点活但单元静默无 schedule；无 golden；注意 spiders/caa=中国自动化学会（侦察簿 L275 撞车注记），非畜牧业协会",
            "e": [f"{REF_RECON}:L275（spider 命名撞车注记）"]},
    "coinmarketcap": {"s": "park", "r": "L3 verified_html(200) 站点活但单元静默无 schedule；无 golden；同域前身 coinmarketcapapi 已于 2026-09-12 退役，本单元为第二代拷贝",
                      "e": [f"{REF_TRIAGE_20260912}（spiders/coinmarketcapapi 归档）"]},
    "cqvip": {"s": "park", "r": "L3 verified_html(200) 站点活但单元静默无 schedule；无 golden；侦察簿列其为在役同类覆盖（cqvip 为可跑代表）",
              "e": [f"{REF_RECON}:L559"]},
    "cssn": {"s": "park", "r": "L3 verified_html(200) 站点活但单元静默无 schedule；无 golden",
             "e": [f"{REF_RECON}:L559"]},
    "digitimes": {"s": "park", "r": "L3 verified_html(200)（digitimes.com/research）站点活但单元静默；无 golden；digitimesresearch 前身已退役，付费墙口径待评估",
                  "e": [f"{REF_TRIAGE_20260912}（spiders/digitimesresearch 归档）"]},
    # —— 静默 × L3 needs_browser / degraded → park（保留代码等出口） ——
    "mof": {"s": "park", "r": "L3 needs_browser（200 但需浏览器出口）；保留代码等非静态出口，出口打通即可 repair",
            "e": []},
    "mysteel": {"s": "park", "r": "L3 degraded（200 但解析面退化）；两段式改版流程已在 README 记录，价值待复核",
                "e": ["spiders/mysteel/README.md（两步 server-rendered 流程说明）"]},
    # —— 静默 × L3 unreachable（均为 TLS/反爬/网关形态，非 NXDOMAIN 域死）→ park ——
    "apipoer": {"s": "park", "r": "L3 unreachable：open.apipoer.com TLS 握手被中断（2 次尝试）——网络级阻断非域死（域名可解析）；不确定→park",
                "e": []},
    "cap": {"s": "park", "r": "L3 unreachable：cap.gov.cn TLS 连接被杀（2 次尝试）——L3 notes 明示 dispatch 分支污染、无法确认存活；非 NXDOMAIN",
            "e": [f"{REF_RECON}:L559"]},
    "chinamonitor": {"s": "park", "r": "L3 unreachable：TLS 握手被拆（2 次尝试）；L3 notes 自述「与 chinamonitor/isee/stlouisfed 同 SSL-EOF 形态，疑似本地网络/TLS 干扰而非确认站点死亡」",
                     "e": []},
    "cisia": {"s": "park", "r": "L3 unreachable：www.cisia.org.cn 502 Bad Gateway（源站/网关侧问题，可恢复）；非域死",
              "e": []},
    "cnia": {"s": "park", "r": "L3 unreachable：chinametal.org.cn 3 URL 全败（SSL EOF/连接重置）；L3 notes 疑 geo-block/TLS 指纹过滤；侦察簿有 cnia 入口参数修复记录（L607），代码面曾校准",
             "e": [f"{REF_RECON}:L607（cnia 参数通道修复记录）"]},
    "csia": {"s": "park", "r": "L3 unreachable：csia.net.cn TLS 握手被拆（2 次尝试）；L3 notes 疑本地网络/TLS 干扰；解析面仅 generic 兜底",
             "e": []},
    "datacenter": {"s": "park", "r": "L3 unreachable：datacenter.org.cn TLS 连接被杀（2 次尝试）；解析面无专门分支（generic 兜底）；非 NXDOMAIN",
                   "e": []},
    "dce": {"s": "park", "r": "L3 unreachable：dce.com.cn 全 URL TLS 中断；L3 notes 明示「需浏览器级 TLS 指纹，本环境静态取数死」——需非静态出口，非域死",
            "e": []},
    "fred-data": {"s": "park", "r": "L3 unreachable：data.fred.stlouisfed.org 全 shard 8 次探测同 SSL EOF；L3 notes 判为 FRED 侧对该客户端/网络的 TLS 过滤而非站点死亡；保留代码等非云出口",
                  "e": []},
    "zce": {"s": "park", "r": "L3 unreachable：czce.com.cn 全 URL 412（反爬挑战，需浏览器/cookie 出口）；L3 notes 明示需浏览器级取数；同域前身 czce 已于 2026-09-12 退役，本单元为第二代拷贝——412 是反爬不是域死，按保守口径 park",
            "e": [f"{REF_TRIAGE_20260912}（spiders/czce 归档，同域前身）"]},
    # —— 静默 × 无 L3 条目 → park + 说明 ——
    "customs-xborder-retail": {"s": "park", "r": "无 L3 条目；侦察簿明载海关 stats.customs.gov.cn「412 瑞数墙」列入放弃清单——强反爬非域死，保留代码等出口",
                               "e": [f"{REF_RECON}:L87（放弃：海关 stats.customs.gov.cn 412 瑞数墙）"]},
    "tariff-hs-rates": {"s": "park", "r": "无 L3 条目，ftatax.mofcom.gov.cn 可达性未验证；静默无 golden；不确定→park",
                        "e": []},
    "data": {"s": "park", "r": "无本单元 L3 条目；同目标 data.cn.gov.cn 的 spiders/cn-gov-data 在 L3 判 unreachable（registry 可查）——目标站对静态探测不可达；静默无 golden",
             "e": ["output/l3-reanalysis/registry.json units[\"spiders/cn-gov-data\"] verdict=unreachable（同目标 data.cn.gov.cn 参照）"]},
    "nbs_gdp": {"s": "park", "r": "2026-10-04 triage 规范保留单元（修复入口+akshare 回退），manifest 注记主动静默无 schedule；output/ 有 2026-10-04 成功产出实证（nbs_gdp.json/nbs_macro.json）；如需常态在产由复核人改 repair 重排 schedule",
                "e": [f"{REF_TRIAGE_20261004}（保留节：spiders/nbs_gdp 规范保留+修复记录）",
                      "spiders/nbs_gdp/output/nbs_gdp.json、nbs_macro.json（2026-10-04 产出）",
                      "data/nbs_gdp.db"]},
    "rmfyalk": {"s": "park", "r": "manifest enabled=false 的登录 stub；真实抓取链路在联邦成员 rmfyalk-case-crawl（跨仓）；本单元只承担站点会话池侧；无 golden",
                "e": ["spiders/rmfyalk/manifest.yaml 注记（2026-10-10 会话池竞争背景）"]},
    # —— 触发式夹具（按设计无 schedule）→ park + 说明 ——
    "auth-smoke": {"s": "park", "r": "认证链冒烟探针（trigger-only），manifest 注记按设计无 schedule；非数据源，park 标记以区别于「待点亮」",
                   "e": ["spiders/auth-smoke/manifest.yaml 注记"]},
    "dispatch-smoke": {"s": "park", "r": "站点派发链冒烟探针（trigger-only，无网络），按设计无 schedule",
                       "e": ["spiders/dispatch-smoke/manifest.yaml 注记"]},
    "tencent-smoke": {"s": "park", "r": "腾讯站点派发探针（trigger-only，无网络），按设计无 schedule",
                      "e": ["spiders/tencent-smoke/manifest.yaml 注记"]},
    "education_card_catalog": {"s": "park", "r": "录入型通道（design D8 禁爬虫规模化），manifest 明示 schedule: null 人工/合作触发；park 标记区别于「待点亮」",
                               "e": ["spiders/education_card_catalog/manifest.yaml 注记（schedule: null 设计说明）"]},
    # —— 在役（有 schedule）× L3 unreachable → 按产出实证分流 ——
    "bls": {"s": "park", "r": "L3 unreachable：www.bls.gov 403（bot protection，探测端被拦）；无 golden、无失败工单，在产健康性未知；不确定→park，若复核最近运行健康可改 repair 维持在产；同域前身 bureauoflaborstatistics 已退役",
            "e": [f"{REF_TRIAGE_20260912}（spiders/bureauoflaborstatistics 归档，同域前身）"]},
    "cisa": {"s": "repair", "r": "有 golden（001-cisa.json）；侦察簿 L286：2026-10-02 落地批次 1 对 cisa 做 chinaisa 门户扩展且 13/13 首爬成功——L3 判定（2026-09-12）早于该实证，属过期 unreachable；schedule 在（29 1 5,15,25 * *），建议 repair 维持在产",
             "e": [f"{REF_RECON}:L286（批次 1 实证）、:L242（chinaisa 门户接入口径）、:L275（cisa/cisia 撞车注记）"]},
    "nbs-stats": {"s": "repair", "r": "有 golden（001-nbs-stats.json）；侦察簿 L286/L622：2026-10-02 批次 1 扩展（流通领域价格）+ 生产双实证（run 1977 行）——L3 403 是探测端反爬而非生产失败；schedule 在（11 4 4,14,24 * *），建议 repair",
                  "e": [f"{REF_RECON}:L286、:L310、:L622（生产双实证）",
                        f"{REF_TRIAGE_20260912}（spiders/nbs-stats-http 归档，同域前身）"]},
    # —— manual 工单未达阈值 → 按根因分流 ——
    "eia-v2": {"s": "repair", "r": "唯一 manual 工单（20261017）根因 rule5:network——api.eia.gov 504/读超时（瞬态网络）；有 golden；schedule 在（0 * * * *）且三周未再现第二张 manual（未升级），判定已自愈；按模块口径「根因可修（network）→repair」",
               "e": [f"{REF_TICKETS}20261017-eia-v2-22ad2230.yaml（manual，category=network，failing_runs 504/timeout）",
                     f"{REF_TICKETS}20261007-eia-v2-b55acdfb.yaml（closed，批次二 Wave B 人工直建 PR#40）"]},
    "entsoe-e": {"s": "park", "r": "唯一 manual 工单（20261007 生成单）= blocked-env：ENTSOE_SECURITY_TOKEN 未到位（brief 明示 token 未到位不启动、缺 token 显式报错）——外部凭据依赖未解除前无法产出；park 等凭据，到位后 repair 复产",
                 "e": [f"{REF_TICKETS}20261007-entsoe-e-d354141e.yaml（manual，blocked-env 判定原文）"]},
    "kosis-openapi": {"s": "park", "r": "唯一 manual 工单（20261007 生成单）= blocked-env：KOSIS_API_KEY 缺失且注册需韩国实名（执行位硬阻断）；单元从未建成（spiders/kosis-openapi 不存在，greenfield 未交付）——park 挂起该生成车道，凭据+实名问题解除后重新发起",
                     "e": [f"{REF_TICKETS}20261007-kosis-openapi-a9ec1cf8.yaml（manual，blocked-env 判定原文+brief）"]},
}


def build_case_facts(repo: Path, tickets_dir: Path, src: str) -> dict:
    """复用 adjudication.build_case_facts 取工单/manifest 事实。"""
    return adjudication.build_case_facts(tickets_dir, repo, src)


def manifest_detail(repo: Path, src: str) -> dict:
    m = Path(repo) / "spiders" / src / "manifest.yaml"
    if not m.is_file():
        return {"exists": False}
    try:
        doc = yaml.safe_load(m.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {"exists": True, "parse_error": True}
    return {
        "exists": True,
        "source_url": doc.get("source_url"),
        "schedule": str(doc["schedule"]) if doc.get("schedule") else None,
        "parked": bool(doc.get("parked")),
        "enabled": doc.get("enabled"),
        "data_period": doc.get("data_period"),
    }


def evidence_pointers(repo: Path, src: str, l3_entry: dict | None,
                      meta: dict, conf: dict, golden: list[str],
                      tickets: list[dict]) -> list[str]:
    pts: list[str] = []
    if l3_entry is not None:
        ev = l3_entry.get("evidence") or {}
        pts.append(f"{REF_L3_REGISTRY.replace('<src>', src)} — "
                   f"verdict={l3_entry['verdict']}，"
                   f"endpoint={l3_entry.get('endpoint')}，"
                   f"probe status={ev.get('status')}，notes={l3_entry.get('notes')}")
        pts.append(REF_L3_REPORT)
    else:
        pts.append(f"{REF_L3_REGISTRY.replace('<src>', src)} — 无本单元条目（L3 横截面未覆盖）")
    if meta.get("exists"):
        pts.append(f"spiders/{src}/manifest.yaml — schedule={meta['schedule']!r}，"
                   f"parked={meta['parked']}，enabled={meta['enabled']}，"
                   f"source_url={meta['source_url']}")
    if (Path(repo) / "spiders" / src / "README.md").is_file():
        pts.append(f"spiders/{src}/README.md")
    for g in golden:
        pts.append(g)
    for e in conf.get("e", []):
        pts.append(e)
    for t in tickets:
        pts.append(f"{REF_TICKETS}{t['ticket_id']}.yaml "
                   f"（terminal={t['terminal']}，category={t['category']}，rule={t['rule'] or '-'}）")
    return pts


def render_case(src: str, reasons: list[str], conf: dict, facts: dict,
                l3_entry: dict | None, meta: dict, golden: list[str],
                now: str) -> str:
    tickets = facts["tickets"]
    pts = evidence_pointers(REPO, src, l3_entry, meta, conf, golden, tickets)
    s = conf["s"]
    lines: list[str] = []
    lines.append(f"# 裁决卷宗：{src}（存量预分类）")
    lines.append("")
    lines.append(f"- 队列状态: escalated（origin={ORIGIN}，存量清账预分类，非工单升级）")
    lines.append(f"- 悬置原因: {'、'.join(reasons)}")
    sug_text = {
        "park": "建议 park（保留代码，摘调度/挂起；保守策略：不确定→park，不建议 retire）",
        "repair": "建议 repair（有成功产出实证，转回在产车道）",
        "retire": "建议 retire（域死/政策停爬，归档退役）",
    }[s]
    lines.append(f"- 机器建议: **{sug_text}**")
    lines.append(f"- 生成/更新时间(UTC): {now}")
    lines.append("")
    lines.append("## 机器预分类建议")
    lines.append("")
    lines.append(f"**{s}** — {conf['r']}")
    lines.append("")
    lines.append("## 证据指针")
    lines.append("")
    for p in pts:
        lines.append(f"- {p}")
    lines.append("")
    lines.append("## 源事实")
    lines.append("")
    if meta.get("exists"):
        lines.append(f"- 调度面: manifest schedule={meta['schedule']!r}"
                     f"（{'在役滚动调度面' if meta['schedule'] else '静默，不在滚动调度面'}）")
        lines.append(f"- manifest 标记: parked={meta['parked']}，enabled={meta['enabled']}，"
                     f"data_period={meta['data_period']!r}")
    else:
        lines.append("- 单元不存在（greenfield 未交付，无 manifest）")
    if l3_entry is not None:
        lines.append(f"- L3 判定（2026-09-12 横截面）: {l3_entry['verdict']} — {l3_entry.get('endpoint')}")
    else:
        lines.append("- L3 判定: 无条目（未覆盖）")
    lines.append(f"- golden 样本: {len(golden)} 个"
                 + (f"（{'; '.join(Path(g).name for g in golden)}）" if golden else "（无 golden 目录）"))
    if facts["last_success_at"]:
        lines.append(f"- 最近一次成功运行: {facts['last_success_at']}（来源: 工单 evidence.last_success）")
    else:
        lines.append("- 最近一次成功运行: unknown（工单语料无成功记录）")
    lines.append("")
    lines.append(f"## 工单史（全部 {facts['total']} 张）")
    lines.append("")
    if tickets:
        lines.append("| 工单 | 创建(UTC) | 终态 | 分类 | 根因线索 |")
        lines.append("|---|---|---|---|---|")
        for t in tickets:
            lines.append(
                f"| {t['ticket_id']} | {t['created_at']} | {t['terminal'] or '(在途)'} "
                f"| {t['category']} | {t['rule'] or '-'} |"
            )
    else:
        lines.append("（无工单——静默单元不在巡检滚动面）")
    lines.append("")
    lines.append("## 裁决选项依据")
    lines.append("")
    lines.append(adjudication._options_rationale(facts))
    lines.append("")
    lines.append("> 本卷宗由 scripts/preclassify_stock.py 机器预分类生成（task 4.1），"
                 "结论以人工批量复核（task 4.2）为准；执行裁决请用 "
                 "`python3 scripts/adjudicate.py decide <src> <repair|park|retire>`。")
    lines.append("")
    return "\n".join(lines)


def build_entry(src: str, reasons: list[str], conf: dict, facts: dict,
                l3_entry: dict | None, meta: dict, golden: list[str],
                evidence: list[str], now: str, threshold: int) -> dict:
    l3_verdict = l3_entry["verdict"] if l3_entry else None
    return {
        "source": src,
        "status": adjudication.STATUS_ESCALATED,
        "origin": ORIGIN,
        "escalated_at": now,
        "threshold": threshold,
        "case_file": f"{adjudication.ADJUDICATION_DIR}/{adjudication.CASES_SUBDIR}/{src}.md",
        "decided_at": None,
        "decision": None,
        "note": None,
        "suspended_reasons": reasons,
        "suggestion": conf["s"],
        "l3_verdict": l3_verdict,
        "schedule": meta.get("schedule"),
        "manual_tickets": facts["manual_count"],
        "total_tickets": facts["total"],
        "manual_ticket_ids": [t["ticket_id"] for t in facts["tickets"]
                              if t["terminal"] == "manual"],
        "last_success_at": facts["last_success_at"],
        "root_causes": {k: v for k, v in facts["rules"].most_common()},
        "categories": {k: v for k, v in facts["categories"].most_common()},
        "evidence": evidence,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=str(REPO), help="仓库根（缺省=脚本上一级）")
    ap.add_argument("--tickets", default=None,
                    help="工单目录（缺省 <repo>/reports/health-tickets）")
    ap.add_argument("--threshold", type=int,
                    default=adjudication.ESCALATION_THRESHOLD)
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印计划，不写任何文件")
    args = ap.parse_args(argv)

    repo = Path(args.repo)
    tickets_dir = Path(args.tickets) if args.tickets else repo / "reports/health-tickets"
    threshold = args.threshold
    now = _utc_now_iso()

    # 1) 悬置三类 → 去重并集
    silent = silent_units(repo)
    unreach_insvc, l3_units = l3_unreachable_in_service(repo)
    below = manual_below_threshold(repo, tickets_dir, threshold)

    reasons_map: dict[str, list[str]] = {}
    for u in silent:
        reasons_map.setdefault(u, []).append("silent")
    for u in unreach_insvc:
        reasons_map.setdefault(u, []).append("l3-unreachable")
    for u in below:
        reasons_map.setdefault(u, []).append("manual-below-threshold")

    # 已在队列（工单升级通道）的源跳过，不与既有卷宗冲突
    queue = adjudication.load_queue(repo)
    by_source = {e["source"]: e for e in queue["entries"]}
    skipped: list[tuple[str, str]] = []
    stock: list[str] = []
    for src in sorted(reasons_map):
        entry = by_source.get(src)
        if entry is not None and entry.get("origin") != ORIGIN:
            skipped.append((src, f"队列已有条目 origin={entry.get('origin')!r}"
                                 f"（工单升级通道），跳过不冲突"))
        elif src not in SUGGESTIONS:
            skipped.append((src, "无预分类配置（SUGGESTIONS 缺条目）——保守起见不入卷宗，请补口径"))
        else:
            stock.append(src)

    # 2) 逐源产卷宗 + 队列条目
    created: list[str] = []
    refreshed: list[str] = []
    for src in stock:
        reasons = reasons_map[src]
        conf = SUGGESTIONS[src]
        facts = build_case_facts(repo, tickets_dir, src)
        l3_entry = l3_units.get(f"spiders/{src}")
        meta = manifest_detail(repo, src)
        gdir = repo / "spiders" / src / "golden"
        golden = [str(p.relative_to(repo)) for p in sorted(gdir.glob("*.json"))] \
            if gdir.is_dir() else []
        evidence = evidence_pointers(repo, src, l3_entry, meta, conf, golden,
                                     facts["tickets"])
        md = render_case(src, reasons, conf, facts, l3_entry, meta, golden, now)
        entry = build_entry(src, reasons, conf, facts, l3_entry, meta, golden,
                            evidence, now, threshold)
        if args.dry_run:
            created.append(src)
            continue
        existing = by_source.get(src)
        if existing is None:
            queue["entries"].append(entry)
            by_source[src] = entry
            created.append(src)
        else:  # stock-preclassify 既有条目：刷新，escalated_at 不漂移
            keep_at = existing.get("escalated_at")
            entry["escalated_at"] = keep_at or now
            existing.update(entry)
            refreshed.append(src)
        p = adjudication.case_path(repo, src)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md, encoding="utf-8")

    if not args.dry_run:
        queue["entries"].sort(key=lambda e: e["source"])
        queue["updated_at"] = now
        adjudication.save_queue(repo, queue)

    # 3) 报告
    dist = Counter(SUGGESTIONS[s]["s"] for s in stock)
    print(json.dumps({
        "silent_units": len(silent),
        "l3_unreachable_in_service": len(unreach_insvc),
        "manual_below_threshold": len(below),
        "stock_cases": len(stock),
        "created": created,
        "refreshed": refreshed,
        "skipped": [{"source": s, "reason": r} for s, r in skipped],
        "suggestion_distribution": dict(dist),
    }, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("(dry-run：未写任何文件)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
