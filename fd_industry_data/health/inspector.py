"""巡检器：遥测（快照文件 / 中央库 psql）→ 分诊 → 工单落仓 → SUBMIT/STATUS 接线。

设计要点：
- 只读遥测；出单是唯一副作用；SUBMIT/STATUS 仅在显式开启时发生。
- 纯 stdlib；中央库读取经 `psql` 子进程（DSN 走环境，避免引入驱动依赖）。
- poster/fetcher 可注入，全部网络调用可离线测试。
"""
from __future__ import annotations

import inspect
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import ticket as ticket_mod
from . import triage
from . import freshness
from . import identity
from .config import (
    VALID_DATA_PERIODS,
    HealthConfig,
    data_period_tolerance_hours,
    frequency_hours,
)

STATE_WORDS = ("queued", "working", "pr-open", "done", "manual")

# SUBMIT 回执语义词表（fix-health-ticket-landing-race）
# 去重回执 = 投递成功（agent 已受理，不重试）；拒收回执 = 投递失败（须重试）。
# 判定按「结论首段」而非全文——回执正文常引述历史错误，全文匹配会误判。
DEDUP_WORDS = ("已见过", "已入队", "重复提交", "已处理")
#: 读不懂的回执允许重投几次。agent 侧按工单路径去重，重投安全；但无限重投会每轮
#: 都打一次门面，所以要有个上限。
_SUBMIT_UNKNOWN_MAX = 3

# SUBMIT 的 turn 是**每 caller+agent 串行**的：实测（2026-11-10）批量提交时第 2 条起
# 全部 HTTP 409 `TURN_IN_FLIGHT`——18 条里 17 条每天重试每天 409，工单永久卡死。
# error 类此前无重试上限，所以这个状态会一直重打。处置：轮到别人占着就等，
# 等不到就**本批停手**（继续捶剩余单只会各自 409），下一个巡检周期接手。
_SUBMIT_TURN_POLL_S = 15.0     # 轮询间隔
_SUBMIT_TURN_BUDGET_S = 90.0   # 单张单的等待预算（实测 agent 回合 20–150s）
# 错误回执上限（fix-turn-fail-starvation，2026-11-15）：agent **真跑过回合**并以
# JSON-RPC 错误收场的投递计满 N 次就停手转人工。不加它时一张处理不完的单会每轮
# 占住唯一槽位跑满整个回合预算（实测 20261107-fred-cpi-ba279d6a-try20：600s 超时），
# 同轮其余单全部 409 deferred、accepted=0——一张单饿死整条线。
# **只数 JSON-RPC 错误回执**（`"code":-32xxx` 的 response）＝回合真的跑过：
#   面层形态不计——`TURN_IN_FLIGHT` 409、`PLAN_EXHAUSTED` 402、裸 HTTP 502/308（这些
#   response 里根本没有 `"code":-32xxx`），免得把通道故障误记成工单罪过。
#   **但 `-32003` 会计**：它是「上游错误原文中继」，实测形态
#   `{"code":-32003,"message":"502: …Upstream service temporarily unavailable"}` 与
#   429 `rate_limit_error` —— 那一次回合确实被受理过、由上游收场，所以计入。
#   这一侧是刻意选的：少计＝一张真卡的单饿死整条线；多计＝被限流挡住的单在 6 轮后
#   判 exhausted 转人工，而判词里写着次数与末次原文，人一眼能看出是限流。
#   **要改这一侧，改注释的同时要改 `test_relayed_upstream_errors_count_toward_cap`**
#   ——别只把 `-32\d{3}` 放宽成白名单，那会把 `-32032 fetch failed`（传输层）也放走。
#   也不设 terminal——工单照常巡检出单，只是 SUBMIT 停手，处置权交回人
#   （判词必须写明停了几次，别让"没投"冒充"没单"）。
_SUBMIT_TURN_FAIL_MAX = 6
# 整批上限：不加这两条实测会撞 workflow 45 分钟超时（2026-11-10 首版修复
# 18 张 × 120s ≈ 36min + 常规 9min = 49m30s，被超时掐成 cancelled，一张都没交成）。
# 有界地做：每轮最多 N 张、批量等待总预算 ≤ B，剩下的下一轮接手。
# 吞吐实测（2026-11-10）：turn 串行，单张 20–150s；workflow 窗口 45 分钟、常规步 9 分钟，
# 故批量等待预算取 30 分钟（20 分钟余量），一轮可清 20 张上下。此前 420s 太保守，
# 一轮只推进 2 张，54 张积压要排一个月。
_SUBMIT_INLINE = True          # SUBMIT 是否附工单全文（现役 agent 无仓读取权限）

_POSTER_RETRY_BACKOFF_S = 3.0  # 连接层失败重试退避（3s/6s 两跳）

_SUBMIT_MAX_TICKETS_PER_RUN = 24
# 预算算的是**墙钟耗时**，不是"等 turn 的 sleep"。实测单次门面调用本身就要约 300s
# （agent 回合串行），旧版只累计 sleep ⇒ 这个预算从未生效：24 张单最坏 24×300s=2 小时。
# 2026-11-12/13/14 三夜跑满 45 分钟墙正是此因，现在每次 poster() 的实际耗时都计入。
_SUBMIT_BATCH_BUDGET_S = 1800.0
# 预判余量：单次调用最坏耗时（实测 ~300s）。开新单前先问"这一张会不会把预算顶穿"，
# 否则在 1799s 处还会再起一次 300s 调用，整批实际耗时就变成 预算 + 一次调用。
_SUBMIT_CALL_RESERVE_S = 330.0
# 单次调用的超时上限（= _default_poster 的默认）。预算见底时要**按剩余预算收缩**
# 每次调用的 timeout：固定 15 分钟 × 3 次连接重试的最坏值是 45 分钟，一次调用就能
# 独占整个作业墙。收缩后「attempts × timeout ≤ 剩余预算」，整批才真的封顶。
_SUBMIT_CALL_TIMEOUT_S = 900.0
_POSTER_ATTEMPTS = 3           # 连接层失败重试次数（实测一轮 10 次里 7 次抖动）
REFUSAL_WORDS = (
    "拒绝入队", "未入队", "不可读", "不认领", "拒绝并转人工",
    "拒绝", "未通过", "无法读取", "不存在",
)


# ── 遥测载入 ──────────────────────────────────────────────────────────────

def load_runs_from_snapshot(path: str | Path) -> list[dict]:
    """快照 JSON：[{source,status,started_at,rows,error,http_status}]。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, list):
        raise ValueError(f"snapshot must be a JSON list: {path}")
    return doc


#: 遥测行的合法状态。用作"这行是不是一条遥测"的判据——比列数更硬：
#: psql 的 `-At` 输出里混进任何非表格文本（驱动告警、连接池报错、Node 调用栈），
#: 列数补齐后都能伪装成一条记录，但它的第 2 列不会是这四个词之一。
_KNOWN_STATUS = frozenset({"success", "failed", "cancelled", "partial",
                           # `blocked-config`（2026-11-10，runner 的 requires_env 预检）：
                           # 源缺必需环境变量，runner 不取数直接记这个状态。它是合法遥测，
                           # 但**既不是跑到也不是失败**——不认它会把它当"非遥测输出"丢掉，
                           # 那些源就会看起来"窗内没跑"，与"缺配置"是两回事。
                           "blocked-config",
                           # `running` 也是合法遥测：这条 run 已经开了但还没结束。
                           # 不认它会把"正在跑"误报成"非遥测输出"——2026-11-09 实测
                           # 真实库里就有一条（education_podcast，开了 10 小时尚未收尾）。
                           # 认了不等于判它：`triage.REACHED` 不含 running，
                           # `_trailing_failed` 只认 failed，所以它只作为记录存在、不参与判词。
                           "running"})

#: 源名形状（slug）。状态列合法但源名是报错文本时，这一行仍不是遥测：
#: 2026-11-07 实测的一段 Node 调用栈就是先落到源名列、再流进 skipped_unknown_period。
_SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def load_runs_from_psql(config: HealthConfig, dsn: str, binary: str = "psql",
                        parse_errors: list[str] | None = None) -> list[dict]:
    """经 psql 子进程读中央库；TSV 输出解析（-A -F '\\t'）。

    只认状态列合法的行。实测（2026-11-07 生产巡检的 `last-run.json`）：
    `skipped_unknown_period` 的第一项是

        'at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1638:16)'

    —— 一段 Node 调用栈被当成了源名。原因是下面把短行补成 7 列，于是任何非表格
    文本都能变成一条"记录"，源名取到整行、状态为空。补列本身是对的（运维的 SQL
    可能少给 watermark 列），但**补列之前必须先确认这行的状态合法**，否则补列就是
    在伪造数据。丢掉的行记在返回值里不方便，就从 stderr 念一句，别静默。
    """
    if not dsn:
        raise ValueError("FD_CENTRAL_PG_DSN is required for --from-pg")
    proc = subprocess.run(
        [binary, dsn, "-At", "-F", "\t", "-c", config.telemetry_sql],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed ({proc.returncode}): {proc.stderr.strip()[:400]}")
    out: list[dict] = []
    rejected = 0
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 2 or cols[1].strip() not in _KNOWN_STATUS:
            rejected += 1
            if parse_errors is not None:
                parse_errors.append(f"non-telemetry line dropped: {line.strip()[:120]}")
            continue
        # 源名必须是 slug：状态列合法但源名是一段报错文本时，这一行仍然不是遥测。
        # 只校验状态列会漏掉「合法状态 + 垃圾源名」这一形态（2026-11-07 实测的
        # Node 调用栈就是先落到源名列、再流进 skipped_unknown_period 的）。
        if not _SOURCE_RE.match(cols[0].strip()):
            rejected += 1
            if parse_errors is not None:
                parse_errors.append(
                    f"non-slug source dropped: {cols[0].strip()[:80]!r}")
            continue
        # 第 7 列为可选 watermark（数据水位/最新数据期），第 8 列为本轮生效的
        # 行数上限（`metrics.limit`）。两条都是"运维扩展 SQL 才有的列"：缺列时补空，
        # 语义是"没记录"，绝不能当成 0——0 会意味着"这轮一行都不许写"。
        cols += [""] * (9 - len(cols))
        source, status, started_at, rows, error, http_status = cols[:6]
        out.append({
            "source": source.strip(),
            "status": status.strip(),
            "started_at": started_at.strip(),
            "rows": int(rows) if rows.strip().lstrip("-").isdigit() else None,
            "error": error.strip(),
            "http_status": int(http_status) if http_status.strip().isdigit() else None,
            "watermark": cols[6].strip() or None,
            "limit": int(cols[7]) if cols[7].strip().isdigit() else None,
            "watermark_state": cols[8].strip() or None,
        })
    if rejected:
        print(f"fd-health: 丢弃 {rejected} 行非遥测输出（状态列不是 "
              f"{'/'.join(sorted(_KNOWN_STATUS))}）", file=sys.stderr)
    return out


def load_watermark_history_from_psql(config: HealthConfig, dsn: str,
                                      binary: str = "psql") -> dict[str, list[tuple]]:
    """读**宽窗**水位历史：``{source: [(started_at: datetime, raw_period: str)]}``。

    只给「推进率」用（2026-11-16）。粗节奏源要在足够长的跨度上才测得出「水位在不在按
    节奏推进」——月频需要 ≥2 个周期＝61 天，而 `telemetry_sql` 的窗只有 30 天，于是
    `uk-hpi`（它的 spider 自己写着「实测最新文件可落后当月 3 个月」）被永久判成冻结。

    **刻意不复用 `telemetry_sql`**：那个窗口还驱动新鲜度、连续失败计数与行数统计，
    放宽它会一次性改掉一批既有判决。这里只取 source/时刻/数据期三列。

    读不到就是读不到：dsn 为空、子进程失败、超时、非零退出，一律返回 ``{}`` ——
    调用方据此退回「只用窗口内运行」的旧口径，而不是把「没读到」当成「没有推进」。
    """
    if not dsn:
        return {}
    try:
        proc = subprocess.run(
            [binary, dsn, "-At", "-F", "\t", "-c", config.watermark_history_sql],
            capture_output=True, text=True, timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"fd-health: 水位历史读取失败（{type(exc).__name__}）——退回窗口内口径",
              file=sys.stderr)
        return {}
    if proc.returncode != 0:
        print(f"fd-health: 水位历史查询失败（{proc.returncode}）："
              f"{proc.stderr.strip()[:200]}——退回窗口内口径", file=sys.stderr)
        return {}
    out: dict[str, list[tuple]] = {}
    for line in proc.stdout.splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        source, started_at, raw = (c.strip() for c in cols[:3])
        if not _SOURCE_RE.match(source) or not raw:
            continue
        try:
            at = datetime.fromisoformat(started_at.replace(" ", "T"))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        out.setdefault(source, []).append((at, raw))
    for points in out.values():
        points.sort(key=lambda item: item[0])
    return out


# 覆盖度行里最多点几个源名：多了没人读，而这一行要能在日志里一眼看完。
_HIST_SAMPLE_SOURCES = 5


def describe_watermark_history(hist: dict[str, list[tuple]],
                               window_sources: "set[str] | None" = None) -> str:
    """宽窗历史的覆盖度，一行。

    **只增可见性**：返回值不参与任何判定（不传给 `plan_tickets`、不进工单正文），
    调用方打完这行就丢。

    为什么必须有这一行（2026-11-17 实历）：推进率只在 ``len(hist) >= 2`` 时才用历史，
    否则静默退回 30 天窗；退回时粗节奏源的判词是 ``推进率 =不可测``——它与「源真的
    停更」在所有可见读数里长得一模一样（顺延单不落盘、日志没有任何点数），于是
    「我这条新判据这轮有没有输入」只能靠人拿 DSN 查库。`uk-hpi` 修法前后两轮判词
    逐字相同，全靠「不可测」三个字才看出来。

    **「历史为空」与「点数不足」处置相反**，所以必须分开报：前者查读方（权限／DSN／
    列路径），后者等时间积累（`metrics->'watermark'->>'period'` 自 2026-11-10 才写入，
    月频源攒够两个点要更久）。

    ``window_sources``：窗口内**有水位**的源集合（判据用 `Run.watermark` 非空）。同一条
    SQL 表达式、更宽的窗口 ⇒ 历史必然包含窗口内每一行，所以「窗口里有水位、历史里
    一行都没有」只可能出在读方（查询失败被吞、列路径变了、权限只覆盖部分行）——
    这一条不变量比点数统计更硬，因此单列一档点名。
    """
    sources = len(hist)
    points = sum(len(v) for v in hist.values())
    thin = sorted(s for s, pts in hist.items() if len(pts) < 2)
    line = f"fd-health: 宽窗水位历史 源 {sources} / 点 {points}"
    if thin:
        sample = "、".join(thin[:_HIST_SAMPLE_SOURCES])
        more = f"…等 {len(thin)} 个" if len(thin) > _HIST_SAMPLE_SOURCES else ""
        line += (f"；{len(thin)} 个源只有 1 个可归一点"
                 f"（推进率将退回窗口内口径）：{sample}{more}")
    elif sources:
        line += "；每源都有 ≥2 个可归一点"
    if window_sources:
        missing = sorted(set(window_sources) - set(hist))
        if missing:
            sample = "、".join(missing[:_HIST_SAMPLE_SOURCES])
            more = f"…等 {len(missing)} 个" if len(missing) > _HIST_SAMPLE_SOURCES else ""
            line += (f"；⚠ 窗口内有水位却不在历史里的源 {len(missing)} 个"
                     f"（同一条 SQL 的更宽窗口必然包含它们 ⇒ 读方有问题）：{sample}{more}")
    return line


def group_runs(records: list[dict]) -> dict[str, list[triage.Run]]:
    grouped: dict[str, list[triage.Run]] = {}
    for r in records:
        run = triage.Run(
            started_at=str(r.get("started_at", "")),
            status=str(r.get("status", "failed")),
            rows=r.get("rows"),
            error=str(r.get("error") or ""),
            http_status=r.get("http_status"),
            watermark=(str(r["watermark"]) if r.get("watermark") else None),
            limit=(int(r["limit"]) if isinstance(r.get("limit"), int) else None),
            watermark_state=(str(r["watermark_state"]) if r.get("watermark_state") else None),
        )
        grouped.setdefault(str(r.get("source", "")), []).append(run)
    for runs in grouped.values():
        runs.sort(key=lambda x: x.started_at)
    return grouped


# ── 周期推断（manifest.data_period 优先，functions.frequency 兜底） ─────────

def manifest_data_period(repo: Path, source: str) -> tuple[str | None, bool]:
    """读 manifest 的 data_period 声明与 parked 注记，返回 (data_period, parked)。

    - manifest 缺失/不可解析 → (None, False)。
    - data_period 须为 day/month/year/irregular（validate_manifests.py 把关枚举）；
      非法值按未声明处理（回退 legacy 推断，不让脏值炸巡检）。
    - parked:true = 裁决 park 注记（add-fleet-lifecycle-adjudication D3），源被
      有意挂起，豁免超期判定。
    """
    manifest = repo / "spiders" / source / "manifest.yaml"
    if not manifest.exists():
        return None, False
    try:
        doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None, False
    period = doc.get("data_period")
    if isinstance(period, str) and period.strip().lower() in VALID_DATA_PERIODS:
        period = period.strip().lower()
    else:
        period = None
    return period, doc.get("parked") is True


def manifest_period_hours(repo: Path, source: str, config: HealthConfig) -> float | None:
    if source in config.expected_period_overrides:
        return float(config.expected_period_overrides[source])
    manifest = repo / "spiders" / source / "manifest.yaml"
    if not manifest.exists():
        return None
    try:
        doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    hours = [
        h for h in (frequency_hours(f.get("frequency")) for f in doc.get("functions") or [])
        if h is not None
    ]
    return min(hours) if hours else None


# 豁免超期的期望周期（parked / data_period=irregular）：freshness/静默规则恒不
# 触发（age <= inf 恒真、age > max(3*inf,48h) 恒假），只剩连续失败判定——阈值
# 沿用 thresholds.consecutive_failures（add-fleet-lifecycle-adjudication 1.3）。
NO_OVERDUE_HOURS = float("inf")


def expected_period_hours(repo: Path, source: str, config: HealthConfig) -> float | None:
    """判「超期未产出」的期望周期（小时）；None = 周期未知（跳过判定）。

    优先级（add-fleet-lifecycle-adjudication 1.3）：

    1. manifest ``parked: true`` 或 ``data_period: irregular`` → inf：豁免超期，
       只判连续失败（连续失败阈值沿用现有逻辑）。
    2. config.expected_period_overrides（运维显式覆盖，小时）——先于 data_period，
       保持既有运维通道语义不变（向后兼容）。
    3. manifest ``data_period: day/month/year`` → 容忍窗 / freshness_multiplier。
       triage 的健康边界 = period * freshness_multiplier，把 period 取成
       容忍窗/multiplier 恰使边界等于容忍窗（day→3d、month→35d、year→370d）：
       超窗即非健康必出单（静默走规则 1b/2，带失败走规则 3~9）。
    4. legacy：manifest functions.frequency 推断（manifest_period_hours）。
    """
    period, parked = manifest_data_period(repo, source)
    if parked or period == "irregular":
        return NO_OVERDUE_HOURS
    if source in config.expected_period_overrides:
        return float(config.expected_period_overrides[source])
    tolerance = data_period_tolerance_hours(period)  # period 非 None 时必命中
    if tolerance is not None:
        return tolerance / config.thresholds.freshness_multiplier
    return manifest_period_hours(repo, source, config)


# ── 出单规划 ──────────────────────────────────────────────────────────────

@dataclass
class Emit:
    source: str
    unit: str
    verdict: triage.Verdict
    runs: list[triage.Run]


@dataclass
class Plan:
    emit: list[Emit]
    deferred: list[dict]          # {source, category, reason}
    healthy: int
    skipped_unknown_period: list[str]
    # 在册（manifest 有 schedule 且未 parked）但遥测窗内零运行的源。
    # 这些源以前**完全不可见**（巡检面 = 遥测里出现过的源），年频源一过 30 天
    # 遥测窗就静默消失——add-flower-vertical 6.1 实测确认的缺口。
    absent: list[dict] = field(default_factory=list)   # {source, reason, data_period}
    # 遥测里有运行、但 `spiders/<source>/manifest.yaml` 根本不存在、也不在
    # `excluded_sources` 里的源。以前它们和"有 manifest 但定不出周期"混在
    # `skipped_unknown_period` 里，区别在于：**后者是本仓的源、前者是没登记的新面孔**。
    #
    # 为什么单列：`excluded_sources` 是手工维护的反向白名单（跨仓特殊 Job 走
    # report_special_run 兜底面），新源冒出来时既没有 manifest 也没进排除表，
    # 就会被静默跳过、谁也不知道。实证 2026-11-09：90 天口径残余是
    # flk-law-backfill/polygon，30 天口径变成 example-stock/yfinance —— 名单会漂，
    # 说明"静默跳过"不是稳定状态。这里把它变成可见的一行字。
    unregistered: list[str] = field(default_factory=list)
    # 最近一次运行是 `blocked-config` 的源（缺必需环境变量，runner 不取数）。
    # 与 `unregistered` 的区别：这些是本仓在册的源，只是缺配置。
    # 必须单列而不能落进 rule2「证据不足 / 需人工」——缺配置不是代码问题，
    # runner 侧（`requires_env` 预检）的设计是「不催修复工单」；
    # 但也不能算健康（没产出）。所以：可见、不判定。
    blocked_config: list[str] = field(default_factory=list)
    # 遥测解析异常（harden-crawl-delivery 2.10）：非遥测行/非 slug 源名不再混进
    # skipped_unknown_period，单独成列。空列表 = 本轮输入干净。
    parse_errors: list[str] = field(default_factory=list)
    # 拆了声明却取不到字段的源（harden-crawl-delivery 3.4）。空 = 所有声明都可兑现。
    declaration_mismatches: list[dict] = field(default_factory=list)
    # 内容仓与源清单的调度状态不一致（7.4）。空 = 两边说的一致。
    inventory_mismatches: list[dict] = field(default_factory=list)
    # 影子模式产出（harden-crawl-delivery 1.8）：新新鲜度判据（按各自 cron 数
    # 「应触发但未成功的周期数」）与旧判据并行运行，**只留痕不出单**。
    # 每项形如 {source, cycles, alerting, first_fire, last_fire, note}。
    # 观察满 3 天无误报后才切为出单判据（tasks 1.8）。
    freshness_shadow: list[dict] = field(default_factory=list)


def _last_success(runs: list[triage.Run]) -> dict | None:
    """最近一次"跑到了"的运行（含 `partial`）。

    工单证据里的 last_success 是给修复 agent 看的"上次拿到数据是什么时候"。
    `partial` = 爬取成功、只是源自己的 accept 条件没满足——数据是拿到了的，
    把它排除会让证据显示"很久没成功"，与实际情况相反。
    """
    for r in reversed(runs):
        if r.status in triage.REACHED:
            return {"started_at": r.started_at, "rows": r.rows}
    return None


def _watermark(runs: list[triage.Run], last_success: dict | None) -> dict:
    """数据水位（最新数据期）证据。

    口径：优先取遥测里最新一个非空 ``watermark``（按时间升序取末个）。中央库
    ``crawl_runs`` 目前无数据期列，故常态是 unknown——**如实标注口径来源，不编造**；
    5.4 覆盖度基线的 staleness 口径（最新数据期）可在遥测 SQL 增加第 7 列后并入。
    """
    latest = None
    for run in runs:
        if run.watermark:
            latest = str(run.watermark)
    if latest:
        return {"latest": latest, "basis": "run telemetry watermark"}
    basis = (
        "unknown（遥测未提供数据期；最近成功时间见 evidence.last_success）"
        if last_success
        else "unknown（遥测未提供数据期；无成功运行记录）"
    )
    return {"latest": None, "basis": basis}


def build_evidence(runs: list[triage.Run], verdict: triage.Verdict) -> dict:
    # 只列**真的失败**。原来写的是 `!= "success"`，于是 `partial`（爬取成功、
    # accept 条件未满足）和 `cancelled`（主动取消）都会被当成 failing run 写进
    # 工单证据——修复 agent 读到的"失败运行"里混着没失败的东西。
    failing = [r for r in runs if r.status == "failed"]
    last_success = _last_success(runs)
    return {
        "window": {"from": runs[0].started_at if runs else "", "to": runs[-1].started_at if runs else ""},
        "failing_runs": [
            {"started_at": r.started_at, "status": r.status, "rows": r.rows,
             "error_summary": r.error[:300], "http_status": r.http_status}
            for r in failing[-10:]
        ],
        "last_success": last_success,
        "watermark": _watermark(runs, last_success),
        "notes": verdict.rule_trace,
    }


def min_rows_floor(repo: Path, source: str) -> int | None:
    """源 manifest 里**显式声明**的「一次成功运行至少应写入多少行」。

    返回 None = 未声明 = 该规则不生效（零回归：0 行成功对某些源是合法的）。
    声明字段：``min_rows_per_run: N``（整数，>0）。

    为什么要显式声明而不是代码里给个默认值：0 行的含义因源而异——节假日无新增、
    幂等 upsert 无改动都可能是正常的；只有源自己知道「我一次至少该写出几行」。
    实证：`flower-auction` 有 4 次 success / rows_written 合计 0，健康级此前无信号。
    """
    path = Path(repo) / "spiders" / source / "manifest.yaml"
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return None
    raw = doc.get("min_rows_per_run")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None
    return int(raw)


def inventory_mismatches(dsn: str, repo: Path) -> list[dict]:
    """来源库与内容仓的调度状态不一致（harden-crawl-delivery 7.4）。

    Measured 2026-11-09: `inmetals` / `metal-com` were parked in git on 11-06
    (commit `e355220`) but `crawl_sources` still carried
    ``schedule='51 4 * * *', enabled=t``. Both sides were "internally fine" —
    git said stopped, the scheduler said due — and nothing ran for 4 days.
    The inspector could not see it because it exempts parked sources.

    Three shapes, all worth a ticket:

    - ``parked-but-scheduled``: manifest parked, inventory still has a schedule
    - ``schedule-drift``: both have a schedule but they differ
    - ``scheduled-but-parked-in-db``: manifest has a schedule, inventory disabled

    Read-only over the DB. A missing DSN means "cannot check", never "no
    mismatches" — the caller decides what to print.
    """
    if not dsn:
        return []
    import yaml

    proc = subprocess.run(
        ["psql", dsn, "-At", "-F", "\t", "-c",
         "select source, schedule, enabled from crawl_sources"],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        print(f"fd-health: inventory mismatch check skipped: "
              f"{proc.stderr.strip()[:200]}", file=sys.stderr)
        return []
    inventory: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        inventory[parts[0].strip()] = {
            "schedule": parts[1].strip(),
            "enabled": parts[2].strip().lower() in ("t", "true"),
        }

    out: list[dict] = []
    for path in sorted((repo / "spiders").glob("*/manifest.yaml")):
        source = path.parent.name
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        manifest_sched = " ".join(str(data.get("schedule") or "").split())
        parked = data.get("parked") is True or data.get("enabled") is False
        row = inventory.get(source)
        if row is None:
            continue  # 未登记：由 unregistered/reconcile_absent 负责
        db_sched, db_enabled = row["schedule"], row["enabled"]
        # 只有「DB 里仍有排期」才是危险态：enqueue_due 的判据是
        # `enabled AND schedule IS NOT NULL`，没有 schedule 就跑不起来。
        # 首版把「enabled=True 但无 schedule」也算进来，30 个里 28 个是这种噪音。
        if parked and db_sched:
            out.append({
                "source": source, "kind": "parked-but-scheduled",
                "manifest": "parked", "inventory": f"schedule={db_sched or '<none>'} enabled={db_enabled}",
                "detail": "内容仓已停用，源清单里仍启用/有排期 —— 调度会认为该跑",
            })
        elif manifest_sched and not db_enabled:
            out.append({
                "source": source, "kind": "scheduled-but-disabled-in-db",
                "manifest": f"schedule={manifest_sched}", "inventory": "enabled=False",
                "detail": "内容仓有排期，源清单里被停用 —— 不会有人跑它",
            })
        elif manifest_sched and db_sched and manifest_sched != db_sched:
            out.append({
                "source": source, "kind": "schedule-drift",
                "manifest": manifest_sched, "inventory": db_sched,
                "detail": "排期不一致 —— 以谁为准需要人确认",
            })
    return out


def scheduled_sources(repo: Path) -> dict[str, dict]:
    """在册源：``spiders/*/manifest.yaml`` 里有 ``schedule`` 且未 ``parked``。

    返回 ``{source: {"data_period": ..., "schedule": ...}}``。这是「应该跑」的集合，
    与遥测里「真的跑过」的集合对账，才能发现「排期在册却整窗没跑」的源。
    """
    out: dict[str, dict] = {}
    for manifest in sorted((repo / "spiders").glob("*/manifest.yaml")):
        try:
            doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            continue
        if not str(doc.get("schedule") or "").strip():
            continue
        if doc.get("parked") is True:
            continue
        period = doc.get("data_period")
        period = (str(period).strip().lower()
                  if isinstance(period, str) and str(period).strip().lower() in VALID_DATA_PERIODS
                  else None)
        out[manifest.parent.name] = {"data_period": period,
                                     "schedule": " ".join(str(doc["schedule"]).split())}
    return out


def reconcile_absent(runs_by_source: dict, repo: Path, config: HealthConfig) -> list[dict]:
    """在册但遥测窗内零运行的源，逐条给出**为什么**（不静默、不混为一谈）。

    两种原因处置完全不同：

    - ``period-longer-than-window``：期别容忍窗比遥测回看窗还长（典型 ``year``
      → 370 天 vs 30 天）。窗内**本就不该有运行**，据此出单是把口径不足伪造成
      故障；只报告，任何策略下都不出单。
    - ``no-run-in-window``：期别短于回看窗却没有一条运行 → 调度面故障
      （CronJob 没渲染 / 站点不认领该源）。按 ``absent_source_policy`` 决定是否出单。
    """
    absent: list[dict] = []
    for source, meta in sorted(scheduled_sources(repo).items()):
        if source in runs_by_source or source in config.excluded_sources:
            continue
        period = meta.get("data_period")
        tolerance = data_period_tolerance_hours(period)
        if tolerance is not None and tolerance > config.lookback_hours:
            reason = "period-longer-than-window"
        elif period is None:
            reason = "period-undeclared"
        else:
            reason = "no-run-in-window"
        absent.append({"source": source, "reason": reason, "data_period": period})
    return absent


#: 声明失效判定需要连续多少次「记了但取不到」。三次约等于日频源三天，
#: 给一次上游换版留出容错；同时要求**每一次都真的记了**，没记的不算。
DECLARATION_MISMATCH_RUNS = 3

_MISMATCH_STATES = ("unavailable", "unknown")


def declaration_mismatches(runs_by_source: dict, repo: Path) -> list[dict]:
    """Declared ``data_period_field`` that the payload never yields (task 3.4).

    A field declared in the manifest but absent from every item is a promise the
    code cannot keep: the watermark silently reads "unavailable" forever, which
    looks the same as "this source has no period". Only runs that actually
    recorded a watermark state count — a run from an older image (``None``)
    proves nothing and must not accumulate towards the threshold.
    """
    out: list[dict] = []
    for source, runs in sorted(runs_by_source.items()):
        manifest = repo / "spiders" / source / "manifest.yaml"
        if not manifest.is_file():
            continue
        try:
            data = yaml.safe_load(manifest.read_text()) or {}
        except (yaml.YAMLError, OSError):
            continue
        field = data.get("data_period_field")
        if not isinstance(field, str) or not field.strip():
            continue
        observed = [r for r in runs if getattr(r, "watermark_state", None)]
        if len(observed) < DECLARATION_MISMATCH_RUNS:
            continue
        tail = observed[-DECLARATION_MISMATCH_RUNS:]
        if all(r.watermark_state in _MISMATCH_STATES for r in tail):
            out.append({
                "source": source,
                "field": field.strip(),
                "runs": len(tail),
                "states": [r.watermark_state for r in tail],
            })
    return out


def plan_tickets(runs_by_source: dict[str, list[triage.Run]], repo: Path, config: HealthConfig,
                 now: datetime, *, dsn: str = "",
                 watermark_history: dict[str, list[tuple]] | None = None) -> Plan:
    emit, deferred, healthy, unknown, unregistered = [], [], 0, [], []
    blocked_config: list[str] = []
    plan_parse_errors: list[str] = []
    absent = reconcile_absent(runs_by_source, repo, config)
    # 遥测里没有的源补成空列表再走同一条分诊链，避免「巡检面 = 遥测里出现过的源」
    # 这个隐性口径（year 频源一过回看窗就静默消失，add-flower-vertical 6.1）。
    ticketable = {a["source"] for a in absent
                  if a["reason"] == "no-run-in-window"
                  and config.absent_source_policy == "ticket"}
    merged = dict(runs_by_source)
    # 只有 ticket 策略下的「该跑却没跑」才进分诊链。report 策略只登记不判——
    # 否则空 runs 会落规则 2「证据不足」needs_human=True，把「分析口径」变成
    # 每天一张重出的人工单（正是 rule1b 注释里记的那 15 张的成因）。
    for entry in absent:
        if entry["source"] in ticketable:
            merged.setdefault(entry["source"], [])
    for source in sorted(merged):
        if source in config.excluded_sources:
            continue
        # 源名不是 slug = 这行输入本身坏了，不是「没登记」：2026-11-07 实测的
        # Node 调用栈先落到源名列，再以「周期解析不出来」的名义流进
        # skipped_unknown_period，读起来像「有个源判不了」。必须在 manifest
        # 存在性判断之前拦下，否则它会先被记成 unregistered。
        if not _SOURCE_RE.match(source):
            plan_parse_errors.append(
                f"non-source identifier dropped: {source[:80]!r}")
            continue
        runs = merged[source]
        # 目录都没有 = 没登记过的源，和"有 manifest 但定不出周期"是两件事，
        # 分开记：前者要人去登记（或排除），后者是本仓 manifest 缺 data_period。
        if not (repo / "spiders" / source / "manifest.yaml").is_file():
            unregistered.append(source)
            continue
        # 最近一次是 blocked-config ⇒ 缺环境变量、runner 根本没取数。
        # 按「源静默」催修是错的归因（那会让修复 agent 去查代码），
        # 但也不该计健康（确实没产出）。单列，可见但不判定。
        if runs and runs[-1].status == "blocked-config":
            blocked_config.append(source)
            continue
        period = expected_period_hours(repo, source, config)
        if period is None:
            unknown.append(source)
            continue
        verdict = triage.classify(source, f"spiders/{source}/", period, runs, now, config.thresholds,
                                  scheduled_absent=source in ticketable,
                                  min_rows_per_run=min_rows_floor(repo, source),
                                  data_period=manifest_data_period(repo, source)[0],
                                  watermark_history=(watermark_history or {}).get(source))
        if verdict.category is None and not verdict.needs_human:
            healthy += 1
            continue
        emit.append(Emit(source=source, unit=f"spiders/{source}/", verdict=verdict, runs=runs))

    # 声明失效（3.4）：manifest 声明了水位字段、但连续 N 次运行都记不到。这些运行
    # 本身是成功的，分诊投票看不见它，所以单独追加一条结构类判词。
    declared_bad = declaration_mismatches(runs_by_source, repo)
    for item in declared_bad:
        source = item["source"]
        if source in config.excluded_sources:
            continue
        emit.append(Emit(
            source=source,
            unit=f"spiders/{source}/",
            verdict=triage.Verdict(
                category="structure",
                needs_human=False,
                suspected_anti_bot=False,
                suggested_action=(f"核对条目键名或更新 data_period_field"
                                  f"（当前声明 {item['field']!r}）"),
                rule_trace=(f"declare:field-mismatch field={item['field']!r} "
                            f"states={item['states']} runs={item['runs']}"),
            ),
            runs=runs_by_source.get(source) or [],
        ))

    # 调度状态不一致（7.4）：内容仓停了但清单仍启用/有排期，或排期对不上。
    # 这类源巡检以前完全看不见（parked 被豁免），实测 zihan 的两个源因此静默 4 天。
    inv_bad = inventory_mismatches(dsn, repo) if dsn else []
    for item in inv_bad:
        source = item["source"]
        if source in config.excluded_sources:
            continue
        if any(e.source == source for e in emit):
            continue  # 已有更强判词，不叠加
        emit.append(Emit(
            source=source,
            unit=f"spiders/{source}/",
            verdict=triage.Verdict(
                category="structure",
                needs_human=False,
                suspected_anti_bot=False,
                suggested_action=("让内容仓与源清单一致：把 crawl_sources 的 "
                                  "schedule/enabled 改成与 manifest 一致"),
                rule_trace=(f"inventory:{item['kind']} manifest={item['manifest']} "
                            f"inventory={item['inventory']}"),
            ),
            runs=runs_by_source.get(source) or [],
        ))

    return Plan(emit=emit, deferred=deferred, healthy=healthy,
                skipped_unknown_period=unknown, absent=absent, unregistered=unregistered,
                blocked_config=blocked_config,
                inventory_mismatches=inv_bad,
                freshness_shadow=shadow_freshness(runs_by_source, repo, now),
                declaration_mismatches=declared_bad,
                parse_errors=plan_parse_errors)


def shadow_freshness(runs_by_source: dict, repo: Path, now: datetime) -> list[dict]:
    """Cadence-aware freshness, recorded only (harden-crawl-delivery 1.8).

    Runs beside the existing judgement and never emits a ticket: the new
    metric is on probation until its false-positive rate is observed for three
    days. Every scheduled, non-parked source gets an entry so the shadow report
    can be compared row-for-row with the tickets the old rules produced.

    A source with no good run and no known start reports ``cycles=0`` plus a
    note — never a fabricated count (see freshness.cycles_since_last_good).
    """
    from .freshness import cycles_since_last_good

    out: list[dict] = []
    for source, meta in sorted(scheduled_sources(repo).items()):
        try:
            data = yaml.safe_load((repo / "spiders" / source / "manifest.yaml").read_text()) or {}
        except (yaml.YAMLError, OSError):
            continue
        schedule = str(data.get("schedule") or "").strip()
        if not schedule:
            continue
        runs = runs_by_source.get(source) or []
        good = [r for r in runs if r.status in getattr(triage, "REACHED", ())]
        last_good = max((r.started_at for r in good), default=None)
        first_seen = min((r.started_at for r in runs), default=None)
        result = cycles_since_last_good(
            schedule=schedule,
            tz=data.get("schedule_tz"),
            last_good_at=_parse_iso(last_good) if isinstance(last_good, str) else last_good,
            created_at=_parse_iso(first_seen) if isinstance(first_seen, str) else first_seen,
            now=now,
        )
        out.append({
            "source": source,
            "cycles": result.cycles,
            "alerting": result.alerting,
            "first_fire": result.first_fire.isoformat() if result.first_fire else None,
            "last_fire": result.last_fire.isoformat() if result.last_fire else None,
            **({"note": result.note} if result.note else {}),
        })
    return out


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _golden_paths(repo: Path, unit: str) -> list[str]:
    gdir = repo / unit / "golden"
    return sorted(f"{unit}golden/{p.name}" for p in gdir.glob("*.json")) if gdir.is_dir() else []


def todays_emitted(outdir: Path, day: str) -> int:
    return len(list(outdir.glob(f"{day}-*.yaml"))) if outdir.is_dir() else 0


def nonterminal_ticket_docs(outdir: Path, within_days: int = 7) -> list[dict]:
    """未终态工单的完整文档（供按 (source, category, signature) 去重）。"""
    docs: list[dict] = []
    if not outdir.is_dir():
        return docs
    cutoff = datetime.now(timezone.utc) - timedelta(days=within_days)
    for p in outdir.glob("*.yaml"):
        try:
            day = datetime.strptime(p.name[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
            doc = ticket_mod.load_ticket(p)
        except (ValueError, Exception):
            continue
        if day < cutoff:
            continue
        if doc.get("terminal") in (None, "queued"):
            docs.append(doc)
    return docs


def nonterminal_sources(outdir: Path, within_days: int = 7) -> set[str]:
    """近 within_days 天内已存在未终态工单（terminal 为 None 或 queued）的源。"""
    found: set[str] = set()
    if not outdir.is_dir():
        return found
    cutoff = datetime.now(timezone.utc) - timedelta(days=within_days)
    for p in outdir.glob("*.yaml"):
        try:
            day = datetime.strptime(p.name[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
            doc = ticket_mod.load_ticket(p)
        except (ValueError, Exception):
            continue
        if day < cutoff:
            continue
        if doc.get("terminal") in (None, "queued"):
            found.add(str(doc.get("source", "")))
    return found


def nonterminal_ticket_paths(outdir: Path, within_days: int = 7) -> list[tuple[Path, dict]]:
    """(path, doc) for every open ticket — needed by the recovery pass, which
    has to rewrite the file in place."""
    found: list[tuple[Path, dict]] = []
    if not outdir.is_dir():
        return found
    cutoff = datetime.now(timezone.utc) - timedelta(days=within_days)
    for p in outdir.glob("*.yaml"):
        try:
            day = datetime.strptime(p.name[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
            doc = ticket_mod.load_ticket(p)
        except (ValueError, Exception):
            continue
        if day < cutoff:
            continue
        if doc.get("terminal") in (None, "queued"):
            found.append((p, doc))
    return found


def _latest_good_run(runs):
    """最近一次「跑到了」的运行（ok/ok_empty，含旧的 partial/success）。"""
    for run in reversed(list(runs or [])):
        outcome = getattr(run, "outcome", None)
        status = getattr(run, "status", None)
        if outcome in ("ok", "ok_empty") or (outcome is None and status in ("success", "partial")):
            return run
    return None


def reconcile_recovered(runs_by_source: dict, repo: Path, outdir: Path, now: datetime,
                        config: "HealthConfig | None" = None,
                        within_days: int = 14) -> list[dict]:
    """Close the loop on tickets whose source recovered on its own (2.9).

    Without this, an open ticket has no exit unless a human merges something —
    11-09 实测 17 张未终态里，有的是源自己恢复了但单子还挂着。The bar is N
    consecutive good runs *after the ticket was opened* (recovery.RECOVER_AFTER_RUNS);
    the decision and the evidence both go into the ticket's lifecycle.
    """
    from . import recovery

    results: list[dict] = []
    for path, doc in nonterminal_ticket_paths(outdir, within_days=within_days):
        source = str(doc.get("source") or "")
        runs = runs_by_source.get(source) or []
        # 新鲜度前置：按该源自身周期给「最近一次成功」一个容忍窗（至少 48h）。
        # irregular 源的周期解析为 inf，会把窗放大成无限——再用 cron 周期判据兜底
        # （拖欠 ≥2 个周期即不算恢复）。
        period = expected_period_hours(repo, source, config) if config else None
        max_age = (max(2.0 * period, 48.0)
                   if period and period != float("inf") else 72.0)
        stale_cycles = None
        meta = scheduled_sources(repo).get(source)
        if not (meta and meta.get("schedule")):
            # 无排期的源谈不上「恢复」：它没有周期，跑那几次是人工/例外触发。
            # 判 recovered 会把「只是没在调度」写成「已经好了」（同日 preflight 实测
            # sec-edgar-facts / hkex-disclosure 即此形状）。
            continue
        if meta and meta.get("schedule"):
            try:
                data = yaml.safe_load(
                    (repo / "spiders" / source / "manifest.yaml").read_text()) or {}
            except (yaml.YAMLError, OSError):
                data = {}
            good = _latest_good_run(runs)
            stale_cycles = freshness.cycles_since_last_good(
                schedule=meta["schedule"], tz=data.get("schedule_tz"),
                last_good_at=_parse_iso(good.started_at) if good else None,
                created_at=None, now=now,
            ).cycles
        decided, evidence = recovery.should_recover(
            doc, runs, now=now, max_age_hours=max_age, stale_cycles=stale_cycles)
        if not decided:
            continue
        ticket_mod.set_terminal(
            doc, "recovered",
            note=(f"连续 {evidence['consecutive_good']} 次成功"
                  f"（阈值 {evidence['threshold']}，自 {evidence['since']} 起算）"),
        )
        path.write_text(ticket_mod.dump_ticket(doc), encoding="utf-8")
        results.append({"ticket": path.name, **evidence})
    return results


def write_plan(plan: Plan, repo: Path, outdir: Path, config: HealthConfig, now: datetime) -> dict:
    """落单：去重（非终态存量）+ 日限额。返回统计。"""
    outdir.mkdir(parents=True, exist_ok=True)
    from . import adjudication
    adjudication.apply_freeze(plan, repo)  # 升级源冻结新单派发（在途不受影响）
    day = now.strftime("%Y%m%d")
    already_today = todays_emitted(outdir, day)
    remaining = max(0, config.max_daily_tickets - already_today)
    existing = nonterminal_sources(outdir)
    existing_docs = nonterminal_ticket_docs(outdir)
    written: list[str] = []
    for item in plan.emit:
        # 顺延条目带上判词类别与规则名：只记 {source, reason} 时，"顺延 7 个"看不出
        # 严重程度——这 7 个里可能有反爬或结构异常，也可能只是需要人工看一眼。
        # 实测（2026-11-09 预演）：12 个发现里 5 个出单、7 个顺延，而顺延的类别
        # 全是既有规则（rule2/3/6a），没有一个是新引入的下限误报——这类信息不该丢。
        #
        # **`rule_trace` 也要带**（2026-11-17）：只留规则名（`rule1d` 三个字）时，
        # 判词里的当事事实值（水位、落后周期、生效判据、窗口内推进率）全部丢失，
        # 而工单 YAML 只在出单时才产生 ⇒ 「顺延的那些判得对不对」每轮都得不到答案。
        # 实历：run 43893532793 的 emit 里 `uk-hpi` 仍是 `rule1d / daily cap reached`，
        # 而「宽窗水位历史到底有没有让它不再命中」这件事，只能靠再派一轮（58 分钟）去猜。
        # 顺延条目恰恰是最该被复核的一批：它们占用了发现，只是没拿到日配额的那 5 张。
        trace = item.verdict.rule_trace or None
        detail = {"source": item.source, "category": item.verdict.category,
                  "rule": (trace.split(":")[0] if trace else None),
                  # 与工单 `evidence.rule_trace` 同源（同一份 verdict），不另算一遍。
                  "rule_trace": trace}
        # 工单身份（harden-crawl-delivery 2.7）：同源 + 同类别 + 同错误签名才算
        # 同一件事；这种情况追加到既有单，不再按「本批新单」重开一张。
        last_error = item.runs[-1].error if item.runs else ""
        signature = identity.signature(last_error)
        if any(identity.is_same_failure(d, item.source, item.verdict.category, signature)
               for d in existing_docs):
            plan.deferred.append({**detail, "reason": "duplicate of open ticket",
                                  "evidence_signature": signature})
            continue
        if item.source in existing and not existing_docs:
            # 兼容：工单文档读不出来时退回旧的按源去重
            plan.deferred.append({**detail, "reason": "nonterminal ticket exists"})
            continue
        if remaining <= 0:
            plan.deferred.append({**detail, "reason": "daily cap reached"})
            continue
        doc = ticket_mod.new_ticket(
            source=item.source,
            unit=item.unit,
            kind="repair",  # 巡检器只产修复单（生成单由人工/工具创建，source-generation-flow）
            evidence=build_evidence(item.runs, item.verdict),
            verify_commands=[],
            category=item.verdict.category,
            needs_human=item.verdict.needs_human,
            suspected_anti_bot=item.verdict.suspected_anti_bot,
            suggested_action=item.verdict.suggested_action,
            golden_paths=_golden_paths(repo, item.unit),
            created_at=now.isoformat(),
            evidence_signature=signature,
        )
        short_id = doc["ticket_id"].split("-")[-1]
        fname = ticket_mod.ticket_filename(day, item.source, short_id)
        doc["ticket_id"] = fname[:-5]
        doc["verify"]["commands"] = [f"python3 scripts/health_verify.py --ticket reports/health-tickets/{fname}"]
        (outdir / fname).write_text(ticket_mod.dump_ticket(doc), encoding="utf-8")
        written.append(fname)
        remaining -= 1
    return {"written": written, "deferred": plan.deferred, "healthy": plan.healthy,
            "skipped_unknown_period": plan.skipped_unknown_period,
            "unregistered_sources": plan.unregistered,
            "blocked_config_sources": plan.blocked_config,
            "absent_sources": plan.absent,
            # 影子模式（1.8）：新判据的逐源结果只落在这里，不进工单队列。
            "parse_errors": plan.parse_errors,
            "declaration_mismatches": plan.declaration_mismatches,
            "inventory_mismatches": plan.inventory_mismatches,
            "freshness_shadow": plan.freshness_shadow,
            "freshness_shadow_alerting": [e["source"] for e in plan.freshness_shadow
                                          if e.get("alerting")]}


# ── 接线：SUBMIT / STATUS（poster/fetcher 可注入） ─────────────────────────

def _unpack_reply(res) -> tuple[int | None, str, dict]:
    """把 poster/fetcher 的回执统一成 `(status, text, trace)`。

    **为什么要有这个函数**：`_default_poster` 自 2026-11-15（865e157）起回**三元组**
    （多带一份跳转痕迹），而 `sync_status` 里写的是 `status, text = read(...)`。
    注入的替身全都只回二元组，所以那一行在测试里永远走不到——真跑时每张单都在
    STATUS 面炸 `ValueError: too many values to unpack (expected 2)`，判词里只剩
    这一行，读起来像网络问题，其实是「通道改了口径、调用方没跟上」。
    （同族：夹具能绕过的那条路径等于没验。）

    宽度按实际长度取：注入的替身回 2 元组照样能跑，不逼所有假实现跟着升级。
    """
    st = res[0] if len(res) > 0 else None
    tx = res[1] if len(res) > 1 else ""
    raw = res[2] if len(res) > 2 else None
    trace = raw if isinstance(raw, dict) else {}
    return st, tx, trace


def _default_poster(url: str, key: str, idem: str, body: str, timeout: int = 900) -> tuple[int, str, dict]:
    """a2a 消息发送：JSON-RPC message/send 封装（文本指令在 parts[0].text）。

    首投可能触发整回合（实测 110s+），SUBMIT 超时放宽到 15 分钟；超时按未完成
    处理：下次运行重投，agent 侧 inbox 去重保证安全（只回执不动手）。
    """
    import uuid
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "kind": "message",
                "messageId": idem,
                "parts": [{"kind": "text", "text": body}],
            }
        },
    }
    data = json.dumps(payload).encode("utf-8")
    hdrs = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Idempotency-Key": idem,
    }
    req = urllib.request.Request(url, data=data, method="POST", headers=hdrs)
    # 网络层失败重试：实测（2026-11-10）一轮 10 次 SUBMIT 里有 7 次直接
    # RemoteDisconnected / URLError，成功率被打到 30%——而每次失败都白占一个 turn 窗口。
    # 只对**连接层**异常重试（HTTPError 是有答复的，不重试）；idem 不变，安全。
    #
    # 3xx 例外（2026-11-15）：POST 撞上 301/302/303/307/308 时 CPython 的
    # HTTPRedirectHandler 不跟随（它只跟 GET/HEAD 的 307/308），于是「前置要求跳转」
    # 在回执里只剩一页 Tengine 的 308 HTML——既不像受理也不像拒收，verdict 落
    # `unknown`，摘要里一个字都没有。实测 2026-11-14 那轮 8 张单有 3 张是这个形状
    # （19:15:51Z / 23:14:05Z / 23:22:05Z），投递根本没到门面（门面同期只记了别的单
    # 的 admitted/refused）。这里自己跟**一跳**，且**只跟同主机同协议**：
    # 跨主机或跨协议的 Location 一律不跟——跨主机等于把 Bearer 凭据送给另一个主机，
    # 跨协议（https→http）等于把同一个凭据明文发上线路，而跳转目标由前置决定、
    # 不由我们决定。
    attempts = _POSTER_ATTEMPTS
    followed = False
    cookies: list[str] = []   # 前置在同一调用内发的挑战 cookie（如 SafeLine sl-session）
    # 跳转痕迹（2026-11-15）：判词此前无条件写「已按同主机跟一跳仍如此」，而**没带
    # Location、或 Location 跨主机/跨协议时我们根本没跟**——那种情形下这句话是假的，
    # 读判词的人会以为已经跟过、转头去查 WAF，而真相是「不知道 308 指向哪」。把实际
    # 发生过的事带出去：跟了几跳、跳去哪、带没带挑战 cookie、为什么没跟。
    trace: dict = {"followed": False, "hops": 0, "location": None, "cookies": 0,
                   "final_url": url, "not_followed": None}
    # 用 while 而不是 for+range：`range` 在循环开始那一刻就把上界算死，
    # 而在里面给跟跳加预算（`attempts += 1`）**不会延长循环**——第一版就是这么写的，
    # 结果「跟跳额外给一次预算」完全没生效，测试直接掉到 `unreachable` 的兜底 raise。
    i = 0
    while i < attempts:
        i += 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read(8000).decode("utf-8", "replace"), trace
        except urllib.error.HTTPError as e:  # noqa: BLE001
            text = e.read(8000).decode("utf-8", "replace")
            loc = e.headers.get("Location") if e.headers else None
            cookies.extend(_challenge_cookies(e.headers))
            trace["location"] = loc
            trace["cookies"] = len(cookies)
            if e.code in (301, 302, 303, 307, 308):
                # 顺序即真相（2026-11-15 线上订正）：原来 `i >= attempts` 排在
                # 「跨主机」之前，于是三次都没能跟（没 Location 或跨主机）时，
                # **最后一跳把真实原因覆写成 `out-of-attempts`**——判词读起来像
                # 「本来能跟、只是预算用完了」，而真相是「从来就没法跟」。
                # 现在先判能否跟，最后才谈预算；确定性答复（没 Location / 跨主机 /
                # 已跟一跳）当场返回，不再拿剩下的重试预算白打三次。
                if not loc:
                    trace["not_followed"] = "no-location"
                elif followed:
                    trace["not_followed"] = "already-followed-once"
                elif not _same_host(url, loc):
                    trace["not_followed"] = "cross-host-or-scheme"
                elif i >= attempts:
                    # 现在只有「连接层失败把预算吃光、最后才拿到 3xx」才走这里；
                    # 而跟跳有独立预算，所以这一档实际已近乎不可达——留着是为了
                    # 万一将来预算语义变了，它至少会说出来而不是静默。
                    trace["not_followed"] = "out-of-attempts"
            if (e.code in (301, 302, 303, 307, 308) and loc and not followed
                    and _same_host(url, loc)):
                followed = True
                trace["followed"] = True
                trace["hops"] += 1
                hop_hdrs = dict(hdrs)
                if cookies:
                    # 2026-11-15 验收轮真跑实证：跟跳带了 Location 仍拿回同一页
                    # 308（verdict_reason「已按同主机跟一跳仍如此」）——WAF 的 308
                    # 是「发 Set-Cookie、让你带 cookie 再来」的挑战，而跟跳原先
                    # 只带原 headers，挑战 cookie 被丢掉，于是必被同一道门挡回。
                    # cookie 只随这一跳去 _same_host 已保证的同一个主机；只在
                    # 本次调用的内存里存活，不跨调用、不跨工单，无会话状态。
                    hop_hdrs["Cookie"] = "; ".join(cookies)
                req = urllib.request.Request(
                    urllib.parse.urljoin(url, loc), data=data, method="POST",
                    headers=hop_hdrs)
                trace["final_url"] = req.full_url
                # 这一跳**额外给一次预算**，不占连接重试的次数（2026-11-15 线上
                # 订正）：原先它和连接层失败共用同一份预算，于是「两跳
                # RemoteDisconnected + 第 3 跳才拿到 308」这种真实形态下，
                # 我们手里握着一个现成的 Location 却没有预算去跟，判词只能写
                # `out-of-attempts`——把「本可以跟」说成了「跳转根本没法处理」。
                # 跟跳最多一次（`followed` 守卫），所以预算上界仍是 +1，不会兜圈。
                attempts += 1
                continue  # 用这份额外预算跟这一跳
            return e.code, text, trace
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if i >= attempts:
                raise
            time.sleep(_POSTER_RETRY_BACKOFF_S * i)
    raise RuntimeError("unreachable: poster retry loop exited without a reply")


def _challenge_cookies(headers) -> list[str]:
    """从响应头抽 Set-Cookie 的 name=value 对（挑战 cookie，如 SafeLine sl-session）。

    只取 name=value；Path/Expires/HttpOnly 等属性丢弃——我们只在**同一次调用**
    内原样带回去，不做任何跨调用的会话状态。没有 Set-Cookie 就是空列表。
    """
    out: list[str] = []
    raws = None
    if headers is not None:
        get_all = getattr(headers, "get_all", None)
        raws = get_all("Set-Cookie") if get_all else headers.get("Set-Cookie")
    if isinstance(raws, str):     # dict 分支拿到的是单条字符串，别逐字符迭代
        raws = [raws]
    for raw in raws or []:
        first = raw.split(";", 1)[0].strip()
        if "=" in first:
            out.append(first)
    return out


def _excerpt(text: str | None, limit: int = 160) -> str:
    """回执体摘要：压掉空白、截断。给判词一个「当事的事实值」。

    只进工单与摘要，不进任何对外通知的正文（见 §12 的展示纪律）。
    """
    flat = " ".join(str(text or "").split())
    return flat[:limit]


def _redact_url(url: str) -> str:
    """跳转目标只留 scheme://host/path，丢掉 query 与 fragment。

    判词要能点名「跳到哪」，但有些前置会把 token 塞进 query（`?ticket=…`），
    那不该进工单与日志。取不到就原样截断到 120 字符。
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return (url or "")[:120]
    if not parts.hostname:
        return (url or "")[:120]
    return f"{parts.scheme}://{parts.netloc}{parts.path}"[:160]


def _same_host(url: str, location: str) -> bool:
    """跳转目标是否与原请求同主机**同协议**（任一不同都不跟）。

    同主机还不够：`https://…` 被跳到 `http://…同名主机` 时主机名相等，但那一跳会把
    `Authorization: Bearer …` 明文送上线。协议必须一致（http→https 的升级也不跟，
    因为那同样是换了一条链路，而跳转目标由前置决定、不由我们决定）。
    """
    a = urllib.parse.urlsplit(url)
    b = urllib.parse.urlsplit(urllib.parse.urljoin(url, location))
    return bool(a.hostname) and a.hostname == b.hostname and a.scheme == b.scheme


def _part_texts(result: dict) -> list[str]:
    """取 result.parts 里所有文本片段（按序）。"""
    parts = result.get("parts") if isinstance(result, dict) else None
    out: list[str] = []
    for part in parts or []:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            out.append(part["text"])
    return out


def _is_thinking(text: str) -> bool:
    """是否为思考片段（非结论）。

    萬星 runner 会把模型的 `<thinking>` 块作为独立 part 前置（2026-10-11 实测：
    STATUS 回执 parts[0] = `<thinking>Let me look at /data...</thinking>`，
    结论在其后的 part）。只取 parts[0] 会拿到思考过程，状态词解析必然为空——
    这是 141/151 次 STATUS 回读拿不到 state 的根因。
    """
    raw = text.strip()
    if not raw:
        return True
    if raw.startswith("<thinking>") or raw.startswith("<think>"):
        return True
    # 整段被思考标签包裹（可能带前后空白或内层标签）
    return bool(re.match(r"^<(thinking|think)>[\s\S]*</\1>\s*$", raw))


def extract_message_text(text: str) -> str:
    """从 a2a JSON-RPC 回执中提取结论文本；非 JSON 原样返回。

    取**最后一个非思考**文本片段（而非 parts[0]）：结论在后、思考在前。
    """
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    if isinstance(doc, dict):
        if doc.get("error"):
            return json.dumps(doc["error"], ensure_ascii=False)
        result = doc.get("result")
        if isinstance(result, dict):
            parts = [p for p in _part_texts(result) if not _is_thinking(p)]
            if parts:
                return parts[-1]
            # 全是思考片段：至少回一段原文，别把整个 JSON 信封当结论
            allparts = _part_texts(result)
            if allparts:
                return allparts[-1]
    return text


def _conclusion_segment(message: str) -> str:
    """取回执的结论首段（第一个 。/；/;/\\n 之前），小写去空白。

    回执正文常引述历史错误（如「说明：某次 submit 曾回 Invalid Request」），
    全文匹配会把这类引述误判为失败——只取结论句。
    """
    seg = re.split(r"[。；;\n]", message.strip(), maxsplit=1)[0]
    return seg.lower().strip()


def _doc_message(doc: dict) -> str:
    """从错误对象里取人话那句（message/msg/detail），取不到返回空串。

    单列出来是给 ``parse_submit_verdict`` 的裸错误分支复用：那一支的错误码
    已经在 reason 里（`JSON-RPC code -32003`），只缺 message。
    """
    for key in ("message", "msg", "detail"):
        val = doc.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _http_error_detail(raw_text: str) -> str:
    """把 HTTP 非 2xx 信封里的错误码带进裁决理由（2026-11-14）。

    门面 a2a 的 402 正文是 ``{"error": {"code": "PLAN_EXHAUSTED", "message": …}}``。
    此前 reason 只记 ``HTTP 402``，于是三种完全不同的成因——余额不足
    （INSUFFICIENT_BALANCE）、调用方被停用（CALLER_SUSPENDED）、套餐分钟用完
    （PLAN_EXHAUSTED）——在巡检报告里长得一模一样，只能回头翻门面代码才对得出
    是哪一种。错误码本来就在正文里，判据没有理由把它丢掉。
    """
    try:
        doc = json.loads(raw_text or "")
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(doc, dict):
        return ""
    code = ""
    text = ""
    err = doc.get("error")
    if isinstance(err, dict):
        code = str(err.get("code") or "")
        text = str(err.get("message") or "")
    elif isinstance(err, str):
        text = err
    if not code:
        for key in ("code", "error_code", "type"):
            val = doc.get(key)
            if isinstance(val, (str, int)) and str(val).strip():
                code = str(val)
                break
    if not text:
        text = _doc_message(doc)
    bits = [b.strip() for b in (code, text) if b and b.strip()]
    if not bits:
        return ""
    detail = ": ".join(bits)[:160]
    return f" · {detail}"


def parse_submit_verdict(status: int, raw_text: str, message: str,
                         trace: dict | None = None) -> dict:
    """把 SUBMIT 回执解析成**结构化裁决**（harden-crawl-delivery 2.11）。

    此前只判「是不是失败」并把回执全文当结论存下来。2026-11-09 实测
    `last-run.json` 里就有这样一条：response 是 agent 的思维过程——

        "The quoting broke. Let me write the python to a file in /tmp ..."

    ——它既不是「已受理」也不是「拒收」，却被记为成功，工单从此不再重投。
    这里把回执分成五类，自由文本只作为原文留档，不充当结论：

    - ``accepted``：明确受理（含状态词 queued/working/pr-open）
    - ``duplicate``：去重回执（已见过/已入队/已处理）——视为已受理
    - ``refused``：明确拒收（拒绝入队/不认领/未通过 …）
    - ``error``：HTTP 非 2xx 或 JSON-RPC error 信封
    - ``unknown``：读不懂。**不算受理**，有限次重试后停手（agent 侧按路径去重，
      重投安全；但无限重投会每轮打一次门面）。
    """
    head = _conclusion_segment(message)
    verdict = "unknown"
    reason = ""
    state = None
    # hollow-ok 前置判据（2026-11-10 实录）：agent 收下了消息但**读不到工单实体**，
    # 回执里明说「工作区不含该仓库 / 无法读取该工单文件 / 请贴 SUBMIT-INLINE」。
    # 这种回执此前因为带「我理解了协议」被判 accepted，工单从此不再重投——
    # 54 张 generate 单就是这样整整一个月没入队。判 refused（可重投）而不是 error，
    # 因为重投是有意义的：正文内联后同一张单就能被处理。
    if status < 400 and _looks_hollow(message or raw_text):
        return {"verdict": "refused", "reason": "hollow receipt: agent 读不到工单实体",
                "state": None, "response_excerpt": (raw_text or "")[:300]}
    if status >= 400:
        verdict, reason = "error", f"HTTP {status}{_http_error_detail(raw_text)}"
    elif 300 <= status < 400:
        # POST 撞上前置跳转（实测 2026-11-14：Tengine 回 308，正文就是那一页 HTML）。
        # 既不是受理也不是拒收，但**必须留下原因**：此前 reason 是空串，摘要里只剩一个
        # `unknown`，看不出「投递根本没到 API」与「agent 回了看不懂的话」的区别。
        #
        # 2026-11-15：判词只许讲**真的发生过的事**。原先这句话无论跟没跟都写「已按同
        # 主机跟一跳仍如此」，而 location 缺失／跨主机时我们根本没跟——读判词的人会被
        # 引去查 WAF，真相却是「不知道这 308 指向哪」。现在按 trace 分档点名。
        verdict = "unknown"
        t = trace or {}
        if not t:
            reason = f"HTTP {status} 跳转未落到 API（调用方未提供跳转痕迹）"
        elif t.get("followed"):
            reason = (f"HTTP {status}：已按同主机跟 {t.get('hops') or 1} 跳"
                      f"（带挑战 cookie {t.get('cookies') or 0} 个、以 POST 重放）"
                      f"仍 3xx，最终 {_redact_url(t.get('final_url') or '')}")
        elif t.get("not_followed") == "no-location":
            reason = f"HTTP {status} 跳转未落到 API（响应没带 Location，无从跟）"
        elif t.get("not_followed") == "cross-host-or-scheme":
            reason = (f"HTTP {status} 跳转未落到 API（Location 指向 " 
                      f"{_redact_url(t.get('location') or '')} —— 跨主机或跨协议，带 Bearer 不跟）")
        elif t.get("not_followed") == "already-followed-once":
            reason = (f"HTTP {status} 跳转未落到 API（已跟一跳后仍 3xx，"
                      f"指向 {_redact_url(t.get('location') or '')}；只跟一跳）")
        else:
            # 2026-11-15 线上订正：这一档此前只说「未跟：out-of-attempts」，
            # **一个字的 Location 都没有**——于是「308 到底指向哪」这个唯一有用的
            # 事实追了三轮还拿不到（同一个形状反复出现，判词却每轮都只说"没跟"）。
            # 现在凡有 Location 就印出来（脱敏：只留 scheme://host/path）。
            why = t.get("not_followed") or "原因未记录"
            loc = _redact_url(t.get("location") or "")
            if loc:
                reason = (f"HTTP {status} 跳转未落到 API（未跟：{why}；"
                          f"Location {loc}）")
            else:
                reason = (f"HTTP {status} 跳转未落到 API（未跟：{why}；"
                          f"响应没给 Location）")
    else:
        try:
            doc = json.loads(raw_text)
            if isinstance(doc, dict):
                if doc.get("error"):
                    # 2026-11-17：信封里的 code/message 此前被整段丢掉，reason 只有
                    # 固定一句 `JSON-RPC error envelope`。health-inspect 连续 5 轮
                    # 在投递段判 outage（`attempted 7 / service_errors 7`），报告里
                    # 读起来像门面挂了；真相是信封里写着 `{"code": -32041,
                    # "message": "turn exceeded 600s"}`——**A2A 每回合 600s 预算被撞穿**，
                    # 与鉴权/网络无关，处置完全不同。同 2026-11-14 那条 402 的教训：
                    # 错误码本来就在正文里，判据没有理由把它丢掉。
                    verdict, reason = "error", f"JSON-RPC error envelope{_http_error_detail(raw_text)}"
                else:
                    # 裸的 JSON-RPC 错误对象：`{"code": -32003, "message": "..."}` ——
                    # 没有 error 外层键。2026-11-09 回放 7 条历史回执里，有 2 条是
                    # 这个形状，此前都被当成「受理成功」，工单从此不再重投。
                    code = doc.get("code")
                    if isinstance(code, int) and code < 0:
                        msg = _doc_message(doc)[:160]
                        verdict, reason = "error", f"JSON-RPC code {code}" + (f" · {msg}" if msg else "")
                    elif doc.get("result") is not None:
                        verdict, reason = "accepted", "JSON-RPC result envelope"
        except (json.JSONDecodeError, TypeError):
            pass
        if verdict != "error":
            if any(k in head for k in DEDUP_WORDS):
                verdict, reason = "duplicate", "dedup receipt"
            elif any(k in head for k in REFUSAL_WORDS):
                verdict, reason = "refused", "refusal receipt"
            elif head.startswith(("invalid request", "unauthorized", "not allowed", "未通过")):
                verdict, reason = "refused", "gate rejection"
            else:
                for word in STATE_WORDS:
                    if word in head:
                        verdict, reason, state = "accepted", f"state {word}", word
                        break
    return {
        "verdict": verdict,
        "reason": reason,
        "state": state,
        "http_status": status,
        # 原文留档：看不懂的回执也要能复盘，但它是证据、不是结论。
        "response_excerpt": (message or "").strip()[:300],
    }


def _looks_failed(status: int, raw_text: str, message: str) -> bool:
    """兼容旧调用点：failed 等价于 verdict ∈ {refused, error}。

    ``unknown`` 不再算成功（那正是本 change 修的缺陷），由调用点按次数上限处理。
    """
    return parse_submit_verdict(status, raw_text, message)["verdict"] in ("refused", "error")


def _submit_body(repo_slug: str, rel: str, path: Path, inline: bool) -> str:
    """构造 SUBMIT 正文。

    **为什么必须内联**（2026-11-10 实测）：协议原形是 ``SUBMIT <repo> <path>``，
    agent 按路径去读仓；但现役 spider-heal agent 的工作区是平台工作区（/app），
    **不含 fd-industry-data 仓库**——它回执原文说「无法直接读取该工单文件，能否用
    SUBMIT-INLINE 把 YAML 发我」。而我们把这种回执判成了 accepted（hollow-ok），
    于是 54 张 generate 单自 2026-10-04 起从未真正入队、也永不重投。

    现在两种都发：先给路径（兼容有仓的部署），再附完整 YAML（给无仓的部署）。
    """
    head = f"SUBMIT {repo_slug} {rel}"
    if not inline:
        return head
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return head
    return (
        f"{head}\n\n"
        f"SUBMIT-INLINE {repo_slug} {rel}\n"
        "（若你的工作区不含该仓库、无法按路径读取，请直接用下面的工单全文，"
        "不要索要更多信息。）\n"
        "```yaml\n" + raw.strip() + "\n```"
    )


HOLLOW_MARKERS = (
    "无法直接读取该工单", "无法读取该工单", "不包含", "无法访问", "读不到",
    "submit-inline", "SUBMIT-INLINE", "工作区", "请在消息中提供", "把工单内容",
)


def _looks_hollow(text: str) -> bool:
    """回执是不是「收到了但读不到工单实体」（hollow-ok 形态）。

    判据取**两条以上**标记同时出现，避免把正常受理回执里偶然出现的「工作区」误判。
    """
    low = (text or "").lower()
    hits = sum(1 for m in HOLLOW_MARKERS if m.lower() in low)
    return hits >= 2


def _is_turn_in_flight(status: int, raw_text: str) -> bool:
    """这次 SUBMIT 回执是不是「本 caller+agent 的 turn 正被占用」。

    409 且带 TURN_IN_FLIGHT 才是；其它 409（例如冲突语义）照常按 error 记。
    """
    if status != 409:
        return False
    return "TURN_IN_FLIGHT" in (raw_text or "").upper()


def _submit_recorded(doc: dict) -> bool:
    """是否已有成功的 SUBMIT 记录（失败回执允许重试）。

    存量的 `ok: true` 记录也会按现行语义重判（fix-health-ticket-landing-race）：
    历史上有拒收回执被误记为成功，若只信 `ok` 字段，这些单永远不会被重投。

    ``unknown``（读不懂的回执，308 HTML 是实例）不算受理，且**重试有上限**
    （``_SUBMIT_UNKNOWN_MAX``）——上限按 unknown 的**条数**数，不按总尝试数：
    unknown 事件全带 `ok: false`，若沿用「遇 ok=False 就 continue」的旧循环，
    上限检查永远走不到，实际形态是**无限重投**（2026-11-15 实测三张 308 单全部
    `False`，docstring 承诺的停手不存在）。已受理/重复不算 unknown；达到上限
    返回 True 即停手转人工。
    """
    attempts = 0
    unknowns = 0
    for e in doc.get("lifecycle") or []:
        if e.get("event") not in ("submitted", "submit-failed"):
            continue
        attempts += 1
        if e.get("event") == "submit-failed":
            continue
        resp = str(e.get("response", ""))
        # 新事件自带 verdict 字段（写事件时已判过）；旧事件回退到重解析存档原文。
        verdict = str(e.get("verdict") or "") or parse_submit_verdict(200, resp, resp)["verdict"]
        if verdict in ("accepted", "duplicate"):
            return True
        if verdict == "unknown":
            unknowns += 1
            if unknowns >= _SUBMIT_UNKNOWN_MAX:
                return True
    return False


def submit_new_tickets(outdir: Path, repo_slug: str, url: str, key: str, config: HealthConfig,
                       poster=None, dry_run: bool = False) -> list[dict]:
    """对尚无 submitted 事件的工单执行 SUBMIT；总闸关闭时不提交（纯巡检）。"""
    poster = poster or _default_poster
    # 注入的 poster 未必收 timeout（测试替身常是 4 参）——只有收得下才收缩超时。
    try:
        _POSTER_TAKES_TIMEOUT = "timeout" in inspect.signature(poster).parameters
    except (TypeError, ValueError):     # 内置/不可反射的调用体：按不收处理
        _POSTER_TAKES_TIMEOUT = False
    results: list[dict] = []
    if not outdir.is_dir():
        return results
    handled = 0
    batch_waited = 0.0
    # 排序：generate 优先且**最新优先**，repair 保持先来先到。
    #  - generate 排在 repair 前：repair 每轮巡检都会重新生成，generate 是业务决定只此一张。
    #  - generate 内部最新优先：生成单携带**时效性证据**（端点实测、参数白名单），一个月前的
    #    侦察结论很可能已过期；旧单不该抢占 turn。实测 2026-11-10：54 张 generate 积压
    #    自 2026-10-04 起从未投出（被 409 卡死），按先来先到要排一个月。
    def _submit_order(path: Path) -> tuple:
        try:
            kind = str(ticket_mod.load_ticket(path).get("kind") or "repair")
        except Exception:  # noqa: BLE001 - 读不了就按 repair 排，别让排序拖垮投递
            kind = "repair"
        if kind == "generate":
            # 文件名以 YYYYMMDD 开头 → 反序即最新优先
            return (0, tuple(-ord(c) for c in path.name[:8]), path.name)
        return (1, (), path.name)

    for p in sorted(outdir.glob("*.yaml"), key=_submit_order):
        # 两条独立上限：张数（turn 串行，多开单只是排队）与墙钟耗时（作业有 60 分钟墙）。
        # 耗时那条留一次调用的余量——否则在预算边缘起的那张单会把整批顶出预算。
        if (handled >= _SUBMIT_MAX_TICKETS_PER_RUN
                or batch_waited + _SUBMIT_CALL_RESERVE_S > _SUBMIT_BATCH_BUDGET_S):
            results.append({
                "ticket": "__batch_limit__",
                "submitted": False,
                "verdict": "deferred",
                "verdict_reason": (f"本批已达上限（{handled} 张 / 已耗时 {int(batch_waited)}s），"
                                   "其余下一轮接手"),
            })
            break
        doc = ticket_mod.load_ticket(p)
        if doc.get("terminal") is not None:
            continue  # 已终态工单不重投
        if _submit_recorded(doc):
            continue
        rel = f"reports/health-tickets/{p.name}"
        entry = {"ticket": p.name, "submitted": False, "reason": ""}
        if not config.master_switch:
            entry["reason"] = "master switch off (inspect-only)"
            ticket_mod.append_event(doc, "submit-skipped", reason=entry["reason"])
        elif dry_run:
            entry["reason"] = "dry-run"
        else:
            # 门面语义：同 Idempotency-Key 重放首答（含错误首答）——曾因首投撞上 agent
            # 不可用而缓存 -32032、此后重投永远拿到旧错误。故键按次递增；工单级去重
            # 由 agent 侧 inbox（按路径）兜底，重投安全。
            prior = sum(
                1 for e in doc.get("lifecycle") or []
                if e.get("event") in ("submitted", "submit-failed")
            )
            turn_fails = sum(
                1 for e in doc.get("lifecycle") or []
                if e.get("event") == "submitted" and not e.get("ok")
                and re.search(r'"code"\s*:\s*-32\d{3}', str(e.get("response") or ""))
            )
            if turn_fails >= _SUBMIT_TURN_FAIL_MAX:
                last = next((str(e.get("response") or "") for e in reversed(doc.get("lifecycle") or [])
                             if e.get("event") == "submitted" and not e.get("ok")
                             and re.search(r'"code"\s*:\s*-32\d{3}', str(e.get("response") or ""))), "")
                entry.update({
                    "submitted": False,
                    "verdict": "exhausted",
                    "verdict_reason": (f"agent 已真跑过回合并以错误收场 {turn_fails} 次（上限 "
                                       f"{_SUBMIT_TURN_FAIL_MAX}），停止重投转人工；末次：{last[:120]}"),
                })
                ticket_mod.append_event(doc, "submit-exhausted", turn_fails=turn_fails,
                                        limit=_SUBMIT_TURN_FAIL_MAX, last_response=last[:300])
                p.write_text(ticket_mod.dump_ticket(doc), encoding="utf-8")
                results.append(entry)
                handled += 1
                continue
            idem = f"{p.name[:-5]}-try{prior + 1}"
            entry["attempt"] = prior + 1
            try:
                body = _submit_body(repo_slug, rel, p, inline=_SUBMIT_INLINE)

                def _post(idem_key: str) -> tuple[int | None, str, float, dict]:
                    """投一次门面，返回 (status, text, 实际耗时秒, 跳转痕迹)。

                    两件事必须一起做，否则预算只是装饰：
                    1) **计时**——实测单次 ~300s（agent 回合串行），只累计 sleep 会让
                       预算永不触发（旧版缺陷，2026-11-12/13/14 三夜跑满墙）；
                    2) **收缩 timeout**——`_default_poster` 默认 900s 且连接层重试
                       3 次，固定超时下一次调用最坏 45 分钟，能独占整个作业墙。
                       按「剩余预算 / 重试次数」给，整批才真的封顶。

                    痕迹是给判词用的（跟没跟、跳去哪）。注入的替身多半只回 2 元组，
                    所以按长度取——**没有痕迹就老实说「未提供」，不假装跟过**。
                    """
                    remaining = max(0.0, _SUBMIT_BATCH_BUDGET_S - batch_waited)
                    t0 = time.monotonic()
                    if _POSTER_TAKES_TIMEOUT:
                        res = poster(url, key, idem_key, body,
                                     timeout=min(_SUBMIT_CALL_TIMEOUT_S,
                                                 remaining / _POSTER_ATTEMPTS))
                    else:
                        res = poster(url, key, idem_key, body)
                    st, tx, trace = _unpack_reply(res)
                    return st, tx, time.monotonic() - t0, trace

                status, text, spent, hop_trace = _post(idem)
                batch_waited += spent
                # turn 被占：有界等待后重试同一张单；预算耗尽则本批停手
                waited = 0.0
                turn_budget = min(_SUBMIT_TURN_BUDGET_S,
                                  _SUBMIT_BATCH_BUDGET_S - batch_waited)
                while _is_turn_in_flight(status, text) and waited < turn_budget:
                    time.sleep(_SUBMIT_TURN_POLL_S)
                    waited += _SUBMIT_TURN_POLL_S
                    batch_waited += _SUBMIT_TURN_POLL_S
                    status, text, spent, hop_trace = _post(f"{idem}-w{int(waited)}")
                    # 重试本身也是一次完整回合，计入单张等待与整批耗时
                    waited += spent
                    batch_waited += spent
                    turn_budget = min(_SUBMIT_TURN_BUDGET_S,
                                      _SUBMIT_BATCH_BUDGET_S - batch_waited)
                if _is_turn_in_flight(status, text):
                    entry.update({
                        "submitted": False,
                        "http_status": status,
                        "verdict": "deferred",
                        "verdict_reason": "turn in flight (409) — 等待预算耗尽，本批停手",
                        "waited_s": int(waited),
                    })
                    ticket_mod.append_event(doc, "submit-deferred",
                                            reason="turn in flight (409)", waited_s=int(waited))
                    p.write_text(ticket_mod.dump_ticket(doc), encoding="utf-8")
                    results.append(entry)
                    break  # 后面每张都会各自 409，继续只会白打门面
                message = extract_message_text(text)
                verdict = parse_submit_verdict(status, text, message, trace=hop_trace)
                ok = verdict["verdict"] in ("accepted", "duplicate")
                entry.update({
                    "submitted": ok,
                    "http_status": status,
                    "verdict": verdict["verdict"],
                    "verdict_reason": verdict["reason"],
                    "state": verdict["state"],
                    # 原文只作留档：看不懂的回执不再冒充结论。
                    "response": verdict["response_excerpt"],
                })
                ticket_mod.append_event(doc, "submitted", http_status=status, ok=ok,
                                        verdict=verdict["verdict"],
                                        response=verdict["response_excerpt"])
            except Exception as e:  # noqa: BLE001 — 网络失败留痕不炸
                entry.update({"reason": f"submit failed: {type(e).__name__}: {e}"[:300]})
                ticket_mod.append_event(doc, "submit-failed", error=str(e)[:300])
        p.write_text(ticket_mod.dump_ticket(doc), encoding="utf-8")
        results.append(entry)
        handled += 1
    return results


# 这些异常算「通道没给出回执」：连不上、超时、TLS 断、HTTP 报文半截，
# 以及 `_default_poster` 重试用尽时自己抛的 RuntimeError（它也是"没收到答复"）。
# 其余异常（ValueError/TypeError/KeyError/AttributeError…）一律是**我们自己的代码**
# 出问题——把它读成通道故障，读的人就会去查出口代理，方向完全反了。
_NETWORK_ERROR_KINDS = frozenset({
    "URLError", "HTTPError", "TimeoutError", "timeout", "socket.timeout",
    "ConnectionResetError", "ConnectionError", "ConnectionRefusedError",
    "ConnectionAbortedError", "BrokenPipeError", "OSError", "SSLError",
    "RemoteDisconnected", "IncompleteRead", "RuntimeError",
})


def _status_failure_kind(row: dict) -> str:
    """STATUS 回读的一行属于哪一档：channel（通道）/ client（我们自己的代码）。

    2026-11-15 实历：`sync_status` 里 `status, text = read(...)` 撞上三元组回执，
    每张单都抛 ValueError；旧判据「有 error 又没有 http_status ⇒ 连不上」把这批
    读成通道整死，`delivery_lane.status` 报 `outage`、原因写「STATUS 回读连不上」。
    一个本地异常伪装成网络故障，会把排查引向完全相反的方向。

    没有 `error_kind` 的老行按通道处置（与旧行为一致，不悄悄放宽）。
    """
    if isinstance(row.get("http_status"), int):
        return "channel" if row["http_status"] >= 500 else "other"
    if not row.get("error"):
        return "other"
    kind = row.get("error_kind")
    if kind is None:
        return "channel"
    return "channel" if str(kind) in _NETWORK_ERROR_KINDS else "client"


def delivery_lane_health(submit: list[dict] | None, status: list[dict] | None = None) -> dict:
    """投递通道体检：**每一次**尝试都栽在服务侧错误 ⇒ 通道断了，不是工单的问题。

    为什么必须单列这一块：2026-11-13~11-14 自愈投递连续三天每次都被平台拒掉
    （`-32032 fetch failed`、`-32003 Connection error`，11-14 我用全量运行实测是
    **HTTP 502**、7 张单 2 分 05 秒全灭），而巡检自己只逐张写「这张单 error」——
    七张单七条 error 读起来像七张单各自的问题，通道死了三天没有任何东西会红
    （最后一次被受理是 2026-11-11T00:08）。同族于「告警系统自己停了没人会知道」：
    沉默与「没有事件」长得一样。

    判据刻意保守，宁可漏判也不误判：只有「每一次尝试都是服务侧错误」才算 outage。
    只要有一张被受理、被拒（`refused`：工单内容问题）或回执看不懂，就只报
    degraded——否则一张内容有问题的单会把整条通道判死。

    `status` 那一半（STATUS 回读）只收**通道级**失败：5xx 与连不上；402/409/429
    是语义拒收，计进去会得到常年红的闸（实测历史分布见函数体）。它参与 degraded
    与 `reasons`，但**不参与 outage**——红线的口径保持原样。
    """
    attempts = [
        r for r in (submit or [])
        if r.get("ticket") != "__batch_limit__" and r.get("http_status") is not None
    ]
    errors = [r for r in attempts if r.get("verdict") == "error"]
    # 「回执看不懂」不能算健康（2026-11-15 实测自伤）：此前这里只收 verdict=="error"，
    # 于是**全部 unknown 的一轮报 status=ok、accepted=0、reasons=[]**——与本文档
    # 头写的「回执看不懂 ⇒ 只报 degraded」正好相反，而且正是最该被看见的一轮
    # （实测 2026-11-14 那轮 8 张单有 3 张撞前置 308，verdict 全落 unknown）。
    # 现在 unknowns 只降级到 degraded、**不进 service_errors**（那个数讲的是"服务侧
    # 报错"，308 是前置跳转不是服务错误），也不判 outage（outage 仍要求全是 error）。
    unknowns = [r for r in attempts if r.get("verdict") == "unknown"]
    # STATUS 回读这一半此前是**死参数**（`status` 收进签名、函数体一次都没读过）：
    # 2026-11-14 那轮实测 16 个 502 / 19 个 409 / 16 个 402 / 13 个 200 —— 三分之一
    # 的回读死在通道层，而体检一个字都没提。但**不能按 `>=400` 收**：实测历史里
    # 主体是 409 `TURN_IN_FLIGHT`（75）、402 计划耗尽（53）、429 限流（31），那些是
    # **语义拒收**，计进去就是一道常年红的闸。通道级的只有两类：5xx，以及
    # `_default_poster` 抛异常（连不上，行里有 `error`、没有 `http_status`）。
    # dry_run 行（`http_status=None` 但一次请求都没发）与 `__status_budget__` 哨兵
    # 必须排除——否则一次校准轮就会报成通道坏了。
    status_rows = [
        r for r in (status or [])
        if not r.get("dry_run") and r.get("ticket") != "__status_budget__"
    ]
    # 「通道坏了」与「我们自己代码坏了」必须分档（2026-11-15 实历）：`sync_status`
    # 里 `status, text = read(...)` 撞上三元组回执，**每张单**都抛 ValueError，
    # 而旧判据「有 error 又没有 http_status ⇒ 连不上」把它整批读成通道故障，
    # delivery_lane 直接报 `outage`、原因写「STATUS 回读连不上」。两个方向的处置
    # 完全相反：通道坏了去查出口/门面，代码坏了回仓里看——混成一档的那天，
    # 差一步就去查出口代理了。所以客户端异常单列，判词点名异常类。
    status_channel = [r for r in status_rows if _status_failure_kind(r) == "channel"]
    status_client = [r for r in status_rows if _status_failure_kind(r) == "client"]
    status_dead = status_channel
    # 同一原因只留一条，避免七行同样的 502 把摘要冲爆
    reasons: list[str] = []
    for r in errors + unknowns:
        why = str(r.get("verdict_reason") or r.get("verdict") or "error")
        code = r.get("http_status")
        label = f"{why} (HTTP {code})" if code is not None else why
        if label not in reasons:
            reasons.append(label)
    for r in status_dead:
        code = r.get("http_status")
        body = str(r.get("response") or "")[:80]
        base = f"STATUS 回读失败 (HTTP {code})" if code is not None else "STATUS 回读连不上"
        # 有回执体就带上：`HTTP 503` 与 `HTTP 503 {"error":{"code":"AGENT_NOT_FOUND"}}`
        # 的处置方向完全不同（一个查通道、一个改路由）——2026-11-15 实历。
        label = f"{base}: {body}" if body else base
        if label not in reasons:
            reasons.append(label)
    for r in status_client:
        label = f"STATUS 回读在自己代码里失败 ({r.get('error_kind') or 'unknown'})"
        if label not in reasons:
            reasons.append(label)
    if not attempts and not status_dead and not status_client:
        verdict = "idle"            # 没东西可投 ≠ 通道坏了
    elif len(errors) == len(attempts) and attempts:
        verdict = "outage"
    elif errors or unknowns or status_dead or status_client:
        verdict = "degraded"
    else:
        verdict = "ok"
    return {
        "status": verdict,
        "attempted": len(attempts),
        "service_errors": len(errors),
        "accepted": sum(1 for r in attempts if r.get("submitted")),
        # 回读这一半只报事实 + 参与 degraded，**不改 outage 口径**（红线仍由 SUBMIT
        # 全灭触发）：先把「回读通道坏了一半」变成可见，再谈它该不该判红。
        "status_reads": len(status_rows),
        "status_channel_errors": len(status_dead),
        # 客户端异常单列一档：它不是通道故障的证据，红绿都不该按它判，
        # 但**必须可见**——否则「代码坏了」会伪装成「网络坏了」继续骗三天。
        "status_client_errors": len(status_client),
        "reasons": reasons[:3],
    }


def parse_status(text: str) -> dict:
    """容错解析 STATUS 回执：优先 JSON，其次关键词扫描。"""
    state, note, pr_url = None, "", ""
    try:
        doc = json.loads(text)
        if isinstance(doc, dict):
            state = str(doc.get("state") or doc.get("status") or "") or None
            note = str(doc.get("note") or "")
            pr_url = str(doc.get("prUrl") or doc.get("pr_url") or doc.get("url") or "")
    except (json.JSONDecodeError, TypeError):
        pass
    if state is None:
        m = re.search(r"\b(queued|working|pr-open|done|manual|merged)\b", text)
        state = m.group(1) if m else None
    if not pr_url:
        m = re.search(r"https?://\S+", text)
        pr_url = m.group(0) if m else ""
    return {"state": state, "note": note[:300], "pr_url": pr_url}


# agent 状态 → 我方终态映射：done=修复完成待人工；manual=转人工；merged=人工已合并（闭环结束→closed）
TERMINAL_MAP = {"done": "fixed-pending-human", "manual": "manual", "merged": "closed"}


# STATUS 回读超时（秒）：实测 agent 回合 20~150s+，曾见 180s 超时；120s 偏紧。
STATUS_READ_TIMEOUT = 300


def _status_idem_key(doc: dict, name: str) -> str:
    """STATUS 幂等键：按工单回读次数递增（每轮唯一，强制真回合取当前状态）。

    门面按 (caller, agent, key) 重放首答，窗口 24h。固定键有两层代价：
    ①一次坏答（空文本/思考答，解析不出 state）会被重放满窗口——2026-10-12
    实证三张 10-11 单被 09:31 的空答重放到次日窗口过期才收敛；
    ②好答重放让状态跃迁滞后（pr-open/done 要等窗口过期才可见）。
    STATUS 是读：每次都要当前状态，故键随生命周期递增（SUBMIT 侧同为递增）。
    lifecycle 只追加，故序号严格递增、永不碰撞。
    """
    prior = sum(
        1 for e in doc.get("lifecycle") or []
        if e.get("event") in ("status", "status-failed")
    )
    return f"status-{name}-r{prior + 1}"


# 单轮 STATUS 回读上限：唯一键让每次回读都走真回合（20~150s+），积压时必须
# 限流，否则顶穿工作流预算（2026-10-12 实证：3 新单 + 3 存量单跑满 19m54s）。
# 取最久未读的 N 张先读；其余下一轮继续（顺序由 _last_read_at 决定，不会饿死）。
STATUS_MAX_PER_RUN = 8

# STATUS 回读的**整体**时间预算（秒）。只有"每张 STATUS_READ_TIMEOUT × 最多 N 张"
# 这一层时，最坏耗时 = 300 × 8 = 2400s，单这一步就能顶破 workflow 的 job 预算：
# 2026-11-12 / 11-13 连续两晚 job 撞 45 分钟墙、deliver 被砍在中途，而
# `if: always()` 的「Commit delivery receipts」仍把半截状态提交进仓（半投递）。
# 有总预算才能在"上游回读变慢"时干净收尾：超预算就停手，剩下的下一轮接手。
STATUS_BUDGET_S = 900.0


def _last_read_at(doc: dict) -> str:
    """该单最后一次 STATUS 回读时间（ISO）；从未读过返回空串（排最前）。"""
    for e in reversed(doc.get("lifecycle") or []):
        if e.get("event") in ("status", "status-failed"):
            return str(e.get("at") or "")
    return ""


def sync_status(outdir: Path, url: str, key: str, fetcher=None,
                max_reads: int = STATUS_MAX_PER_RUN,
                budget_s: float = STATUS_BUDGET_S,
                dry_run: bool = False) -> list[dict]:
    """回读未终态工单的 STATUS，映射进生命周期与终态枚举。

    单轮最多读 max_reads 张，优先最久未读的（含从未读过的）；且**总耗时**不超过
    budget_s——每次回读的超时取「剩余预算」与 STATUS_READ_TIMEOUT 的较小值，预算
    用尽即停手（剩下的下一轮接手）。这层总预算不是优化，是防"被外面一刀砍死"：
    被 job 墙砍断时，前面的状态已经写进工单文件，`if: always()` 的回执步会把半截
    结果照样提交，形成"投递做了一半"的混合态。

    ``dry_run``：只报「本来会读哪几张」，**一次请求都不发、一个文件都不写**。
    2026-11-14 之前 `--dry-run` 只覆盖 SUBMIT，而 STATUS 回读同样是一次真 turn：
    校准轮既花模型钱，又和 SUBMIT 抢同一个 (caller, agent) 槽位（当天线上回执里
    有一批 409 `TURN_IN_FLIGHT`，正是这种自撞）。校准模式的含义是「不产生外部
    副作用」，只覆盖一半就等于没有校准语义。
    """
    results: list[dict] = []
    if not outdir.is_dir():
        return results
    pending: list[tuple[str, Path, dict]] = []
    for p in sorted(outdir.glob("*.yaml")):
        doc = ticket_mod.load_ticket(p)
        if doc.get("terminal") is not None:
            continue
        pending.append((_last_read_at(doc), p, doc))
    pending.sort(key=lambda t: (t[0], str(t[1])))  # 最久未读优先（空串=从未读）
    if dry_run:
        # 排序与筛选照走（这样报出来的就是真跑时会读的那几张），只是不发请求。
        return [{"ticket": p.name, "state": None, "http_status": None,
                 "dry_run": True, "would_read": True}
                for _at, p, _doc in pending[:max_reads]]
    deadline = time.monotonic() + budget_s
    for _at, p, doc in pending[:max_reads]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            results.append({
                "ticket": "__status_budget__",
                "state": None,
                "error": f"本批已达 STATUS 时间预算（{int(budget_s)}s），其余下一轮接手",
            })
            break
        if fetcher is None:
            timeout = min(float(STATUS_READ_TIMEOUT), remaining)

            def read(u, k, idem, body, _t=timeout):
                return _default_poster(u, k, idem, body, timeout=_t)
        else:
            read = fetcher
        try:
            status, text, trace = _unpack_reply(
                read(url, key, _status_idem_key(doc, p.name[:-5]),
                     f"STATUS reports/health-tickets/{p.name}"))
        except Exception as e:  # noqa: BLE001 — 回读失败留痕不炸
            ticket_mod.append_event(doc, "status-failed", error=str(e)[:300])
            p.write_text(ticket_mod.dump_ticket(doc), encoding="utf-8")
            # error_kind 让体检分得出「通道坏了」与「我们自己代码坏了」——
            # 见 `_status_failure_kind`：两个方向的处置相反。
            results.append({"ticket": p.name, "state": None, "error": str(e)[:200],
                            "error_kind": type(e).__name__})
            continue
        parsed = parse_status(extract_message_text(text))
        # 痕迹只在真发生过跳转时才落账：每张单都写一份空 trace 会把生命周期刷成噪音。
        extra = {"trace": trace} if trace.get("followed") or trace.get("location") else {}
        # 4xx/5xx 的**回执体**是判因的唯一线索（2026-11-15 实历）：STATUS 面 8 次全 503，
        # 而只看状态码时「目标 agent 路由不存在」与「通道坏了」长得一模一样——真相是
        # connector-identity 那次滚版把 slug 派生改了，老 slug `packs-…-spider-heal`
        # 在 `/api/wanxing/v1/agents`（18 个）里根本没有，而巡检只写了「HTTP 503」。
        # 判词带不上当事的事实值，读的人就会朝错的层修（同族：308 不印 Location）。
        if isinstance(status, int) and status >= 400:
            extra["response"] = _excerpt(text)
        ticket_mod.append_event(doc, "status", http_status=status, state=parsed["state"],
                                note=parsed["note"], pr_url=parsed["pr_url"], **extra)
        if parsed["state"] in TERMINAL_MAP:
            # STATUS 回读的 "merged" 对应人工合并，故走人工路径（closed 是人工门）。
            ticket_mod.set_terminal(doc, TERMINAL_MAP[parsed["state"]], note=parsed["note"],
                                    human=(parsed["state"] == "merged"))
        p.write_text(ticket_mod.dump_ticket(doc), encoding="utf-8")
        results.append({"ticket": p.name, "http_status": status, **parsed, **extra})
    return results