"""发布节奏级 SLA：区分「上游还没发布」与「我方断流」（add-flower-vertical 6.2）。

为什么需要这一层：健康级（``health/``）只会判「这条源还活着吗」——它用
``data_period`` 乘一个倍数当容忍窗，**不认上游的发布时点**。对斗南这种 T+1 发布
的日频源，这会把两件完全不同的事混成一件：

* 上游周五没发（节假日/跳日），我们抓到了页面、内容没更新 → **源未发布**，
  无需任何处置，等下一轮；
* 我们自己的 CronJob 没跑 / 出口断了 / 会话失效 → **源已断流**，必须出单。

混在一起的结果是二选一都错：都当故障=每天重出无效单；都当正常=真断流没人管。

本模块是纯逻辑（无 IO、无时钟偷取），实测参数全部由调用方注入，便于离线演练。

学习口径（冷启动）：

* 只用 ``status == "success"`` 且**带水位**的运行样本来学「发布滞后 = 运行日 − 数据日」
  （上限取 p95，不用均值：上游偶发延迟不该把整条源的期望窗拉长）；
* 样本 < ``min_samples`` 时不判——返回 ``window_learning``，**显式标注「窗口学习中」
  而不是猜**。冷启动期出「未发布」告警等于编造 SLA。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Sequence

STATES = ("published", "awaiting_publication", "stream_broken",
          "window_learning", "unknown")


@dataclass(frozen=True)
class Run:
    """一次抓取运行。``watermark`` = 本次运行看到的最新数据日（无则 None）。"""

    started_at: str
    status: str                       # success | failed
    rows: int | None = None
    watermark: str | None = None      # YYYY-MM-DD


@dataclass(frozen=True)
class Cadence:
    """发布节奏声明（全部来自 config，代码内无阈值）。"""

    period_days: float                # 上游发布周期（日频=1）
    min_samples: int = 5              # 冷启动门槛
    lag_percentile: float = 0.95      # 学习用的滞后分位
    lag_trim: float = 0.1             # 先剔掉最高这一比例的滞后样本（见 learn_lag_days）
    grace_hours: float = 6.0          # 越过期望时点后的宽限
    stalled_multiplier: float = 2.0   # 超过 period*该倍数没跑成功 = 断流


@dataclass(frozen=True)
class State:
    state: str
    detail: str
    watermark: str | None = None
    expected_next_by: str | None = None
    learned_lag_days: float | None = None
    samples: int = 0

    @property
    def needs_attention(self) -> bool:
        return self.state == "stream_broken"


def _ts(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("empty sample")
    if len(ordered) == 1:
        return float(ordered[0])
    pos = (len(ordered) - 1) * max(0.0, min(1.0, q))
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return float(ordered[low]) * (1 - frac) + float(ordered[high]) * frac


def learn_lag_days(runs: Iterable[Run], cadence: Cadence) -> tuple[float | None, int]:
    """学「发布滞后」：运行日 − 数据日（天）。返回 (滞后上界, 样本数)。

    口径：**先剔掉最高 ``lag_trim`` 比例的样本，再取 ``lag_percentile`` 分位**。
    只取裸 p95 在小样本下会被单次异常直接拉满（6 个样本里 1 个 10 天延迟就把上界
    抬到 8 天），而期望窗一旦被抬宽就再也不会收回——之后真断流也判不出来。剔尾
    后这个值读作「覆盖历史绝大多数次发布的滞后」。
    """
    lags: list[float] = []
    for run in runs:
        if run.status != "success":
            continue
        data_date = _as_date(run.watermark)
        if data_date is None:
            continue
        run_date = _ts(run.started_at).date()
        lags.append((run_date - data_date).days + 0.0)
    if not lags:
        return None, 0
    ordered = sorted(lags)
    drop = int(round(len(ordered) * max(0.0, min(0.5, cadence.lag_trim))))
    if drop and len(ordered) - drop >= 1:
        ordered = ordered[: len(ordered) - drop]
    return _percentile(ordered, cadence.lag_percentile), len(lags)


def classify(runs: Sequence[Run], cadence: Cadence, now: datetime) -> State:
    """判一次：published / awaiting_publication / stream_broken / window_learning。"""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if not runs:
        return State("unknown", "无运行记录：先确认该源是否已点亮（schedule/CronJob）")

    ordered = sorted(runs, key=lambda r: _ts(r.started_at))
    lag, samples = learn_lag_days(ordered, cadence)
    last_success = None
    for run in ordered:
        if run.status == "success":
            last_success = run
    if last_success is None:
        return State("stream_broken", "从未成功过一次（排期在册却零成功，属流断在抓取侧）",
                     samples=samples)

    if samples < cadence.min_samples or lag is None:
        return State(
            "window_learning",
            f"窗口学习中：带水位的成功样本 {samples} < {cadence.min_samples}，"
            "发布滞后窗尚未学出（此处不判「未发布」，冷启动期不出告警）",
            watermark=last_success.watermark, samples=samples,
        )

    watermark = _as_date(last_success.watermark)
    if watermark is None:
        return State("window_learning",
                     "窗口学习中：最近的运行没有带回数据日水位，无法算发布滞后",
                     samples=samples)

    expected_date = watermark + timedelta(days=cadence.period_days + lag)
    expected_next_by = datetime(expected_date.year, expected_date.month, expected_date.day,
                                tzinfo=timezone.utc) + timedelta(hours=cadence.grace_hours)
    if now <= expected_next_by:
        return State(
            "published",
            f"水位 {watermark.isoformat()}，按学出的滞后 {lag:.1f} 天+宽限 "
            f"{cadence.grace_hours:g}h，下一期期望到 {expected_next_by.isoformat()}，当前正常",
            watermark=watermark.isoformat(),
            expected_next_by=expected_next_by.isoformat(),
            learned_lag_days=lag, samples=samples,
        )

    # 已过期望时点：必须分清「我们没跑/跑挂了」与「我们跑了但上游没发」。
    # 判据不用「最近一次运行是什么时候」，而用**每次运行本应看到哪一期**：
    # 运行日 X（滞后 L）本应至少看到 X − L 这一期；如果某次**成功**运行看到的水位
    # 仍早于 X − L，那才是「我们跑到了、上游没发」的硬证据。反之，若所有成功运行
    # 都各自拿到了该拿的那一期，说明我们只是**在水位之后没再跑过**（调度侧）。
    expected_for = lambda run: _ts(run.started_at).date() - timedelta(days=lag)  # noqa: E731
    observed_miss = [r for r in ordered
                     if r.status == "success" and expected_for(r) > watermark]
    tail_failed = [r for r in ordered
                   if _ts(r.started_at) > _ts(last_success.started_at) and r.status != "success"]
    if tail_failed:
        first = tail_failed[0]
        return State(
            "stream_broken",
            f"水位停在 {watermark.isoformat()}（期望 {expected_next_by.isoformat()} 前有下一期），"
            f"此后有 {len(tail_failed)} 次失败运行（首条 {first.started_at}）→ 断在抓取侧",
            watermark=watermark.isoformat(),
            expected_next_by=expected_next_by.isoformat(),
            learned_lag_days=lag, samples=samples,
        )
    if observed_miss:
        witness = observed_miss[-1]
        return State(
            "awaiting_publication",
            f"水位 {watermark.isoformat()}，期望 {expected_next_by.isoformat()} 前出下一期；"
            f"运行 {witness.started_at} 成功、按学出的滞后 {lag:.1f} 天本应看到 "
            f"{expected_for(witness).isoformat()}，实际仍是 {watermark.isoformat()}"
            " → 上游未发布（我方抓取正常）",
            watermark=watermark.isoformat(),
            expected_next_by=expected_next_by.isoformat(),
            learned_lag_days=lag, samples=samples,
        )
    return State(
        "stream_broken",
        f"水位停在 {watermark.isoformat()}，期望 {expected_next_by.isoformat()} 前有下一期；"
        f"水位之后没有一次成功运行（最近成功 {last_success.started_at}）"
        " → 调度侧没在跑（不是上游未发布：我们根本没去取）",
        watermark=watermark.isoformat(),
        expected_next_by=expected_next_by.isoformat(),
        learned_lag_days=lag, samples=samples,
    )
