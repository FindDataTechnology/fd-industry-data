"""确定性分诊：遥测 runs -> category / needs_human 判定（无随机、无 IO）。

输入 ``runs`` 按时间升序（``classify`` 内部仍会按 started_at 防御性重排）。
只考察“尾部连续 failed 段”（最近一次成功之后的连续失败）；更早的失败不参与
分诊。``source`` / ``unit`` 仅随工单携带，规则本身不依赖它们。

规则（优先级 3 > 4 > 5 > 6a > 6b > 7 > 9 > 8；规则 1/1b/2 先于以上）：

1. 健康短路：最近一次 success 距今 <= expected_period_hours *
   freshness_multiplier，且尾部连续 failed < consecutive_failures -> 健康
   （Verdict(None, needs_human=False)，不出单）。
1b. 源静默（health-threshold-calibration 2026-10-13 新增）：尾部连续 failed
   = 0 且最近 success 距今 > max(3*period, 48h) -> "contract"，
   action=重新侦察调度面。遥测无失败样本却长期沉默，说明**调度侧停止触发**
   （排期丢失/站点停摆/注册漂移），不是爬虫代码问题——按证据不足出单只会
   每天重出（两周校准期 15 张此类单的实证）。
2. 证据不足：尾部 failed 样本 < min_runs_for_verdict 且不满足健康 ->
   needs_human=True（不猜分类）。
3. 反爬签名（最高优先，任一尾部失败命中即定，不允许被多数票淹没）：最近失败含
   http_status in {403, 429}，或 error 命中
   waf|challenge|captcha|forbidden|access denied|cloudflare|瑞数|云盾|人机
   -> category="contract", suspected_anti_bot=True，
   action="更换数据表面（官方 API/替代入口）或退役；禁止逆向"。
4. 源死亡：error 命中 dns|nodename|resolve|not found|404|410|gone|refused|domain
   （或 http_status in {404, 410}），且最近 success 距今 > 7 *
   expected_period_hours（或无 success）-> "source-dead"，action="退役评估"。
5. 网络层：error 命中 timeout|timed out|connect|reset|econn|tls|ssl|unreachable|
   502|503|504（或 http_status in {502, 503, 504}）-> "network"，
   action="出口切换/代理复测（不改代码）"。
6a. 疑似源改版（securities-source-alerting 2026-11-07 新增）：源可达（http_status
   in {200, None}，且未被 4/5 判为源死亡/网络故障）但失败模式指向**端点结构变化**：
   golden drift / schema mismatch / 字段或列缺失 / 选择器失效 / 解析零行（rows==0
   且为结构类错误）/ 返回非 JSON。-> "source-revision"，action=先取证核对端点结构
   再更新 parser 与 golden 样本。与 "network" 严格互斥（超时/5xx/DNS/连接拒绝
   在规则 4/5 已先行判走，不会落到本规则）。
6b. 结构层（残余）：error 命中 parse|json decode|keyerror|selector|no element|table|
   column|解码|empty，且 http_status in {200, None}，但没有 6a 的结构变异证据
   （典型=本地解析器崩溃/未知解析错误）-> "structure"，action="定向修 parser"。
7. 契约层：error 命中 400|401|param|parameter|invalid|endpoint|moved|redirect|
   schema|版本（或 http_status in {400, 401}）-> "contract"，
   action="重新侦察数据表面"。
8. 分歧/未命中：未归类票占多数，或两类票数相同 -> needs_human=True，
   category=None（人工分诊）。
9. 兜底：尾部失败全部为同一未知模式（错误指纹相同）且 >= consecutive_failures
   次完全相同错误指纹 -> "fallback"，action="整段重生成（兜底）"。

判定方式：先做规则 3 的签名短路；否则对尾部连续 failed 段逐 run 按 4~7 顺序取
第一个命中规则（6a 先于 6b），再多数投票（None 未命中作为一票参与）；未命中占多数
或平票走规则 8 转人工（规则 9 优先于规则 8）。``rule_trace`` 记录命中规则路径，供
工单 evidence 引用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

ANTIBOT_ACTION = "更换数据表面（官方 API/替代入口）或退役；禁止逆向"
SOURCE_DEAD_ACTION = "退役评估"
NETWORK_ACTION = "出口切换/代理复测（不改代码）"
SOURCE_REVISION_ACTION = "疑似源改版：先取样本核对端点结构（字段/选择器/golden），再更新 parser 与样本（非网络故障）"
STRUCTURE_ACTION = "定向修 parser"
CONTRACT_ACTION = "重新侦察数据表面"
FALLBACK_ACTION = "整段重生成（兜底）"
SILENT_ACTION = "重新侦察调度面：核对 schedule/站点心跳/注册行（遥测静默，非代码问题）"
ABSENT_ACTION = "重新侦察调度面：排期在册但整窗没有一次运行（查 CronJob 是否渲染/站点是否认领该源）"
EMPTY_SUCCESS_ACTION = (
    "核对解析是否只读到表头/空表：最近的成功运行写入行数低于该源声明的 "
    "min_rows_per_run（成功但没数据，不是「没有新数据」，也不是 0 值）")
HUMAN_ACTION = "人工分诊（失败模式分歧/无法归类）"
INSUFFICIENT_ACTION = "补充遥测样本后复检（证据不足）"

_ANTIBOT_HTTP = (403, 429)
_DEAD_HTTP = (404, 410)
_NET_HTTP = (502, 503, 504)
_CONTRACT_HTTP = (400, 401)

_ANTIBOT_RE = re.compile(
    r"waf|challenge|captcha|forbidden|access denied|cloudflare|瑞数|云盾|人机|\b(?:403|429)\b",
    re.IGNORECASE,
)
_DEAD_RE = re.compile(
    r"dns|nodename|resolve|not found|\b(?:404|410)\b|gone|refused|domain",
    re.IGNORECASE,
)
_NET_RE = re.compile(
    r"timeout|timed out|connect|reset|econn|tls|ssl|unreachable|\b(?:502|503|504)\b",
    re.IGNORECASE,
)
_STRUCT_RE = re.compile(
    r"parse|json decode|keyerror|selector|no element|table|column|解码|empty",
    re.IGNORECASE,
)
# 6a 疑似源改版：端点可达但payload形状变化的显式证据（先于 contract/6b 判定）。
# 不收录裸 "schema"/"版本"（那是 contract 的重侦察语义），只收 mismatch/drift/缺失类。
_REVISION_RE = re.compile(
    r"golden\s*(?:drift|mismatch|changed?|diff)|schema\s*(?:mismatch|drift|changed?|break)"
    r"|字段(?:缺失|丢失|不存在)|(?:missing|absent|unexpected)\s+(?:field|column)"
    r"|no\s+(?:such\s+)?(?:field|column)|column\s+(?:missing|absent)"
    r"|选择器(?:失效|未命中|不存在)|selector\s*(?:not\s*found|missing|broken?|failed)"
    r"|no element|零行|zero\s*rows|0\s*rows|empty\s+(?:result|payload|response|dataset)"
    r"|no\s+rows|解析(?:结果)?(?:为)?空|返回空|unexpected\s+(?:html|token)"
    r"|not\s+(?:valid\s+)?json|html\s+(?:page|instead)"
    r"|表头(?:异常|变更|漂移)|列(?:缺失|不存在|变更)|布局变更|页面改版|端点改版",
    re.IGNORECASE,
)
_CONTRACT_RE = re.compile(
    r"\b(?:400|401)\b|param|parameter|invalid|endpoint|moved|redirect|schema|版本",
    re.IGNORECASE,
)


#: 算作"这次跑到了"的状态。`partial` = 爬取成功但 accept 条件未满足，
#: 数据不完整而非没跑——巡检的活性判据必须把它算进去（见 last_success 处注释）。
REACHED = frozenset({"success", "partial"})
_REACHED = REACHED  # 旧名保留：内部与既有引用继续可用


@dataclass(frozen=True)
class Run:
    """单次运行遥测。started_at 为 ISO8601；error 为错误摘要文本（可空）。

    ``watermark`` 是本次运行观测到的数据水位（最新数据期，如 "2026-09-30"）；
    遥测源不提供时为空——工单 evidence.watermark 如实标注 unknown 而非编造。
    """

    started_at: str
    status: str  # "success" | "failed"
    rows: int | None = None
    error: str = ""
    http_status: int | None = None
    watermark: str | None = None
    # 本次运行生效的行数上限（`crawl_runs.metrics.limit`）。None = 未记录，
    # 历史 run 全是 None。它决定了 `rows` 能不能跟"全量应有的行数"比：
    # 用 limit=20 跑出来 20 行，不是低产，是跑满了。
    limit: int | None = None


@dataclass(frozen=True)
class Thresholds:
    """分诊阈值（集中声明，便于 inspector/config 覆盖）。"""

    consecutive_failures: int = 3
    freshness_multiplier: float = 2.5
    min_runs_for_verdict: int = 3


@dataclass(frozen=True)
class Verdict:
    """分诊结论。category=None 且 needs_human=False 表示健康（不出单）。"""

    category: str | None
    needs_human: bool
    suspected_anti_bot: bool
    suggested_action: str
    rule_trace: str


def _parse_ts(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _fingerprint(text: str) -> str:
    """错误指纹：小写、数字折叠为 #、空白归一，用于“完全相同错误”判定。"""
    normalized = re.sub(r"\d+", "#", (text or "").lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _snip(text: str, limit: int = 120) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fmt_hours(value: float | None) -> str:
    return "none" if value is None else f"{value:.2f}"


def _matches_anti_bot(run: Run) -> bool:
    if run.http_status in _ANTIBOT_HTTP:
        return True
    return bool(_ANTIBOT_RE.search(run.error or ""))


def _trailing_failed(runs: list[Run]) -> list[Run]:
    """尾部连续 failed 段（时间升序返回）。"""
    tail: list[Run] = []
    for run in reversed(runs):
        if run.status == "failed":
            tail.append(run)
        else:
            break
    tail.reverse()
    return tail


def _classify_run(run: Run, stale: bool) -> str | None:
    """对单次失败按 4~7 的顺序取第一个命中；未命中返回 None（规则 3 已短路）。"""
    error = run.error or ""
    if (_DEAD_RE.search(error) or run.http_status in _DEAD_HTTP) and stale:
        return "source-dead"
    # 规则 5 先行：超时/连接类/5xx 永远不是"源改版"（与网络故障严格互斥）。
    if _NET_RE.search(error) or run.http_status in _NET_HTTP:
        return "network"
    # 规则 6a：可达（200/未标注，且未被网络/死亡判走）但结构变异证据成立。
    # 解析零行（rows==0 + 结构类错误）也算改版证据；rows 未知（None）不猜。
    if run.http_status in (200, None):
        if _REVISION_RE.search(error) or (run.rows == 0 and _STRUCT_RE.search(error)):
            return "source-revision"
    # 规则 6b：残余结构类（无端点结构变化证据的本地解析错误）。
    if _STRUCT_RE.search(error) and run.http_status in (200, None):
        return "structure"
    if _CONTRACT_RE.search(error) or run.http_status in _CONTRACT_HTTP:
        return "contract"
    return None


_NOTES = {
    "source-dead": (4, SOURCE_DEAD_ACTION),
    "network": (5, NETWORK_ACTION),
    "source-revision": ("6a", SOURCE_REVISION_ACTION),
    "structure": ("6b", STRUCTURE_ACTION),
    "contract": (7, CONTRACT_ACTION),
}


def classify(
    source: str,
    unit: str,
    expected_period_hours: float,
    runs: list[Run],
    now: datetime,
    thresholds: Thresholds = Thresholds(),
    *,
    scheduled_absent: bool = False,
    min_rows_per_run: int | None = None,
) -> Verdict:
    """确定性分诊入口。规则与优先级见模块 docstring。

    ``scheduled_absent``（add-flower-vertical 6.1）：该源 manifest 里**有 schedule**
    但整个遥测窗内没有一条运行记录。这时 ``runs`` 为空、尾部失败=0，落到规则 2
    「证据不足」是错判——证据不足指「有失败样本但不够判」，而这里是**一次都没跑**，
    属调度面故障（CronJob 没渲染 / 站点不认领 / 期别声明与排期矛盾），
    应判 contract 并给出可执行的侦察动作。

    ``min_rows_per_run``（add-flower-vertical 6.6）：该源 manifest 显式声明的
    「一次成功运行至少应写入多少行」。未声明（None）时本规则完全不生效——这是
    零回归的关键：0 行成功对某些源是合法的（节假日无新增、幂等 upsert），
    只有源自己声明了下限后，「成功但行数不够」才算故障。
    实证来源：`flower-auction` 有 **4 次 success / rows_written 合计 0**，
    而健康级此前对此**完全无信号**（零行成功只在错误文本命中结构异常时才被看见）。
    """
    ordered = sorted(runs, key=lambda run: _parse_ts(run.started_at))
    period = float(expected_period_hours)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # 规则 0：排期在册但整窗零运行（必须先于规则 1/1b/2——那三条都以「有运行记录」为前提）
    if scheduled_absent and not ordered:
        trace = (
            "rule0:scheduled-but-never-ran 遥测窗内零运行记录"
            f"（期别期望窗={_fmt_hours(period * thresholds.freshness_multiplier)}h）"
        )
        return Verdict("contract", False, False, ABSENT_ACTION, trace)

    # `partial` 算"跑到了"：它是"爬取成功但源自己声明的 accept 条件没满足"
    # （数据不完整，不是没跑）。巡检判的是"这条源还活着吗"——把 partial 当没成功，
    # 会让一个每天正常在跑的源被判成"极久未成功"→ rule2 需人工，纯噪声。
    # 实测（2026-11-09）：三次 partial、最近 2h 前跑过 → 旧逻辑判 needs_human；
    # 同数据改成 success 就健康。零行判定仍走规则 1c（它只看成功且行数够不够）。
    last_success = None
    for run in ordered:
        if run.status in _REACHED:
            last_success = run
    last_success_age = (
        None
        if last_success is None
        else (now - _parse_ts(last_success.started_at)).total_seconds() / 3600.0
    )

    tail = _trailing_failed(ordered)
    tail_failed = len(tail)

    # 规则 1c（6.6）：成功但行数低于声明下限。必须**先于**规则 1 的健康短路——
    # 否则「跑成功、时间新鲜、只是什么都没写出来」会被判健康，正是要抓的那种静默。
    #
    # 判据是**最近一次成功**，不是窗口内任意一次成功（2026-11-09 修）。原来的实现扫全窗口，
    # 于是历史上一次异常低产会让该源一直挂单，直到那条 run 滑出回看窗——给 38 个源声明下限时
    # 实测有 10 个会因此上线即出单，而它们最近一次都正常。规格
    # （source-output-floor）承诺的是「成功但写出 0 行要点单」和「上游少给几条不得误报」，
    # 都不支持让一次陈年异常永久生效；而且本规则的 trace 文案一直写的是"最近成功"，
    # 与实际行为对不上。按最近一次判：坏的那次出单，下一次正常即自动消解。
    if min_rows_per_run is not None and last_success is not None:
        floor = int(min_rows_per_run)
        # 只跟"跑得动 floor"的运行比：本轮上限 < floor 的运行**物理上**写不满，
        # 拿它判低产是错的（2026-11-06 那批 48 个源各写 20 行就是这么被误读的）。
        # 上限未记录（历史 run）时照旧判——那是"不知道"，不是"不可比"。
        comparable = [
            run for run in ordered
            if run.status in _REACHED
            and (run.limit is None or run.limit >= floor)
        ]
        last_comparable = comparable[-1] if comparable else None
        if last_comparable is None:
            last_comparable = last_success   # 全被上限压住：退回原判据，不动行为
        latest_rows = last_comparable.rows if last_comparable.rows is not None else 0
        if latest_rows < floor:
            # 历史低位只作诊断信息，不参与判定：它解释"这个源一向如此"还是"这次突然掉"。
            historical = [
                run for run in comparable
                if run is not last_comparable
                and (run.rows if run.rows is not None else 0) < floor
            ]
            skipped_by_limit = sum(1 for run in ordered
                                   if run.status in _REACHED and run.limit is not None
                                   and run.limit < floor)
            trace = (
                "rule1c:success-but-too-few-rows "
                f"最近一次可比成功 rows={latest_rows}"
                f" < min_rows_per_run={floor}（{last_comparable.started_at}）"
                + (f"；窗口内另有 {len(historical)} 次低于下限" if historical else "")
                + (f"；已跳过 {skipped_by_limit} 次上限低于下限的运行" if skipped_by_limit else "")
            )
            return Verdict("structure", False, False, EMPTY_SUCCESS_ACTION, trace)

    # 规则 1：健康短路
    fresh_cutoff = period * thresholds.freshness_multiplier
    fresh = last_success_age is not None and last_success_age <= fresh_cutoff
    if fresh and tail_failed < thresholds.consecutive_failures:
        trace = (
            f"rule1:healthy last_success_age={_fmt_hours(last_success_age)}h"
            f"<={_fmt_hours(fresh_cutoff)}h"
            f" tail_failed={tail_failed}<{thresholds.consecutive_failures}"
        )
        return Verdict(None, False, False, "", trace)

    # 规则 1b：源静默（health-threshold-calibration 2026-10-13 新增）。
    # 遥测里没有任何失败样本、但最近成功远超 3 个周期（且至少 48h）——这不是
    # 「样本不足」，是**调度侧停止触发**（排期丢失/站点停摆/注册漂移）。两周
    # 校准期的 15 张「证据不足」重出单全部是这一形态（inmetals/metal-com/
    # flower-auction，根因=enqueue_due 的 5 分钟网格盲区）；按规则 2 每天重出
    # 转人工，没有诊断价值。判 contract（契约层）：修复路径=重新侦察调度面
    # （核对 schedule/站点心跳/注册行），不是改爬虫代码。
    if tail_failed == 0 and last_success_age is not None:
        silent_cutoff = max(3.0 * period, 48.0)
        if last_success_age > silent_cutoff:
            trace = (
                f"rule1b:source-silent tail_failed=0"
                f" last_success_age={_fmt_hours(last_success_age)}h"
                f">{_fmt_hours(silent_cutoff)}h(=max(3*period,48h))"
            )
            return Verdict("contract", False, False, SILENT_ACTION, trace)

    # 规则 2：证据不足
    if tail_failed < thresholds.min_runs_for_verdict:
        trace = (
            f"rule2:insufficient-evidence tail_failed={tail_failed}"
            f"<min_runs_for_verdict={thresholds.min_runs_for_verdict}"
            f" last_success_age={_fmt_hours(last_success_age)}h"
        )
        return Verdict(None, True, False, INSUFFICIENT_ACTION, trace)

    # 规则 3：反爬签名（最高优先，签名即定）
    for index, run in enumerate(tail):
        if _matches_anti_bot(run):
            trace = (
                f"rule3:anti-bot run#{index + 1}/{tail_failed}"
                f" http_status={run.http_status!r} error={_snip(run.error)!r}"
            )
            return Verdict("contract", False, True, ANTIBOT_ACTION, trace)

    # 规则 4：源死亡的前置条件（最近 success 距今 > 7*expected 或无 success）
    stale = last_success_age is None or last_success_age > 7.0 * period

    labels = [_classify_run(run, stale) for run in tail]

    # 规则 9：兜底（全部未命中的同一未知模式，且达到连续失败阈值）
    if all(label is None for label in labels) and tail_failed >= thresholds.consecutive_failures:
        fingerprints = {_fingerprint(run.error) for run in tail}
        if len(fingerprints) == 1 and next(iter(fingerprints)):
            trace = (
                "rule9:fallback"
                f" identical-unknown-fingerprint x{tail_failed}"
                f" (>=consecutive_failures={thresholds.consecutive_failures})"
                f" fingerprint={_snip(next(iter(fingerprints)))!r}"
            )
            return Verdict("fallback", False, False, FALLBACK_ACTION, trace)

    # 投票（None 未命中作为一票）
    counts: dict[str | None, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    best = max(counts.values())
    winners = [label for label, count in counts.items() if count == best]

    if len(winners) != 1:
        trace = (
            f"rule8:divergent tie labels={counts} tail_failed={tail_failed}"
            f" last_success_age={_fmt_hours(last_success_age)}h"
        )
        return Verdict(None, True, False, HUMAN_ACTION, trace)

    winner = winners[0]
    if winner is None:
        trace = (
            f"rule8:unclassified-majority labels={counts} tail_failed={tail_failed}"
            f" last_success_age={_fmt_hours(last_success_age)}h"
        )
        return Verdict(None, True, False, HUMAN_ACTION, trace)

    rule_no, action = _NOTES[winner]
    trace = (
        f"rule{rule_no}:{winner} votes={counts[winner]}/{tail_failed}"
        f" labels={counts}"
        + (f" stale_success_age={_fmt_hours(last_success_age)}h>{_fmt_hours(7.0 * period)}h"
           if winner == "source-dead" else "")
    )
    return Verdict(winner, False, False, action, trace)