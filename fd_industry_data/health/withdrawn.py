"""暂撤 `min_rows_per_run` 的三源：窗口 + 撤声明时的原下限 + 从遥测算窗口态。

为什么单独一个模块
------------------
窗口日期与 floor 在**两个**脚本里都要用：`scripts/min_rows_observation.py` 从遥测
（`crawl_runs`）算，`scripts/check_withdrawn_sources.py` 从 k8s Job 算。写成两份字面量
必然各自漂移——本仓已有「同一事实写在四面会各自漂移」的旧账，所以这里只留一处。

floor 为什么写死在常量里
------------------------
撤声明时 manifest 里那三行 `min_rows_per_run` 已删。运行时去读 manifest 只会拿到
`None`，而 `None` 会被当成「未声明、不判」——静默失效，正是要防的那种。
所以把撤除时的**原值**随窗口一起记下来（25 / 25 / 30）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class Window:
    """一个源的恢复窗口：窗口之后**新建**的那次运行才算数。"""

    source: str
    at: datetime
    floor: int      # 撤声明时的 min_rows_per_run 原值


WITHDRAWN: tuple[Window, ...] = (
    Window("gb-standards", datetime(2026, 11, 12, 2, 25, tzinfo=CST), 30),
    Window("cisa", datetime(2026, 11, 15, 1, 29, tzinfo=CST), 25),
    Window("boxofficemojo", datetime(2026, 11, 17, 3, 14, tzinfo=CST), 25),
)

#: 判定为「未恢复」的态。`pending` 不在里面——窗口未到不能判，提前报红＝天天误报。
RED = frozenset({"low_rows", "failed", "incomplete", "no_new_job"})


def _ts(s: object) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def window_states(records: list[dict], now: datetime,
                  windows: tuple[Window, ...] = WITHDRAWN) -> list[dict]:
    """每个源一条：窗口之后那次运行的结论。

    六态，只有 `complete` 算通过：
      complete / low_rows / failed / incomplete / no_new_job / pending

    为什么 `low_rows` 单列而不并进 complete：跑成功但行数低于下限，正是当初那三张
    误报单的形态。并进去就等于把老毛病放回来。
    """
    rows: list[dict] = []
    for w in windows:
        mine: list[dict] = []
        for r in records:
            if str(r.get("source")) != w.source:
                continue
            at = _ts(r.get("started_at"))
            if at is None:
                continue
            mine.append({"at": at, "status": str(r.get("status") or ""), "rows": r.get("rows")})
        mine.sort(key=lambda x: x["at"])

        last_success = None
        for x in reversed(mine):
            if x["status"] in ("success", "partial"):
                last_success = x
                break
        in_win = [x for x in mine if x["at"] >= w.at]

        if now < w.at:
            verdict = "pending"
            why = f"窗口未到（还有 {(w.at - now).total_seconds() / 3600:.1f}h）"
        elif not in_win:
            verdict = "no_new_job"
            if last_success is None:
                why = "窗口后无记录（该源从无成功运行）"
            else:
                why = (f"窗口后无记录（最近 success "
                       f"{last_success['at'].astimezone(CST):%m-%d %H:%M}，"
                       f"rows={last_success['rows']}）")
        else:
            tail = in_win[-1]
            stamp = tail["at"].astimezone(CST).strftime("%m-%d %H:%M")
            if tail["status"] == "failed":
                verdict, why = "failed", f"窗口后最新为 failed（{stamp}）"
            elif tail["status"] == "running":
                verdict, why = "incomplete", f"窗口后最新为 running（{stamp}，未终态）"
            elif tail["status"] in ("success", "partial"):
                n = tail["rows"] if isinstance(tail["rows"], int) else 0
                if n >= w.floor:
                    verdict, why = "complete", f"窗口后 success rows={n} ≥ floor {w.floor}"
                else:
                    verdict, why = "low_rows", f"窗口后 success 但 rows={n} < floor {w.floor}"
            else:
                verdict, why = ("incomplete",
                                f"窗口后最新状态 {tail['status'] or '空'} 非终态（{stamp}）")

        rows.append({
            "source": w.source,
            "window": w.at.isoformat(),
            "verdict": verdict,
            "why": why,
            "red": verdict in RED,
            "last_success": None if last_success is None else {
                "at": last_success["at"].isoformat(),
                "rows": last_success["rows"],
            },
        })
    return rows
