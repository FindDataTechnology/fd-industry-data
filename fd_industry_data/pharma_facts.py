"""医药事实的定型与修订窗绑定（本 change 第 4 阶段）。

本模块**不重写** ``fact-ledger-kernel`` 的 R1–R8，只把医药三类事实的周期语义映射到
既有实现（``fd_open_data_mcp.fetch.finalization``）与既有接入字段
（manifest ``revision_policy`` / ``finalize_grace_days``、``Concept.finalize_grace_days``）。

映射表（与 change spec ``pharma-fact-ledger`` 的表格逐行对应）
--------------------------------------------------------------
| 事实类 | 粒度 | grace | 定型时点（由 finalize_limit 算出） |
|---|---|---|---|
| 药品批准文号 | month | 0 | 批准日期所在月的**次月首日** |
| 医药目录条目 | annual | 0 | 年度版基准年**次年 12-31 之后**（即第三年元旦） |
| 临床试验注册 | month | 0 | 首次注册（起始）月的次月首日 |

为什么目录条目用 ``annual`` 而不是 ``month``
-------------------------------------------
目录条目绑定的是**目录年度版**（2025 年版、2026 年版），不是某个月的观测。若按
month 粒度定型，某一年的条目会在次月首日被判定型，而实际次年目录发布时官方仍会
调整归类——那样修订会被判成"改写已定型事实"，只能永久保存一份过时但自洽的旧值，
且因为该期已定型，陈旧告警也不会响。``annual`` 的基数是次年 12-31，正好覆盖
"次年目录发布前仍可修订"这一真实节奏。

一处刻意的差异（必须写清，避免读者以为算错）
------------------------------------------
change spec 写目录条目"次年目录发布时定型"，而 ``annual`` 粒度给的是"次年 12-31"。
后者更**保守**（留得更久可修订），方向与 R1 一致——宁可多留修订窗，不可提前锁死；
真正的定型时点取决于官方发布节奏，无法用固定日期表达，故取上界。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from fd_open_data_mcp.fetch.finalization import (
    FinalizeWindow,
    finalize_limit,
    is_finalized,
    period_end,
)

#: 事实类 → (粒度, grace_days, 修订策略)
#: 修订策略取既有允许集合里的值（见 ingest_declarations.REVISION_POLICIES）：
#: 三类医药事实都是"官方会改历史"，必须留痕，故均为 overwrite_with_history；
#: 不能用 immutable——那会让官方注销/变更永远进不来。
FACT_CLASSES: dict[str, tuple[str, int, str]] = {
    "drug_approval": ("month", 0, "overwrite_with_history"),
    "catalog_entry": ("annual", 0, "overwrite_with_history"),
    "clinical_trial": ("month", 0, "overwrite_with_history"),
}


@dataclass(frozen=True)
class FactTiming:
    """一类医药事实的周期语义。"""

    fact_class: str
    granularity: str
    grace_days: int
    revision_policy: str

    def window(self) -> FinalizeWindow:
        return FinalizeWindow(self.grace_days)

    def period_end(self, period: str) -> date | None:
        return period_end(period, self.granularity)

    def finalize_limit(self, period: str) -> date | None:
        """该期仍可修订的最后一天；之后即为定型。"""
        return finalize_limit(period, self.granularity, self.window())

    def is_finalized(self, period: str, *, now: datetime | None = None) -> bool:
        return is_finalized(period, self.granularity, self.window(), now=now)


def timing_for(fact_class: str) -> FactTiming:
    try:
        granularity, grace, policy = FACT_CLASSES[fact_class]
    except KeyError:
        raise ValueError(
            f"unknown pharma fact class {fact_class!r}; "
            f"known: {sorted(FACT_CLASSES)}"
        ) from None
    return FactTiming(fact_class, granularity, grace, policy)


def manifest_declarations(fact_class: str) -> dict[str, object]:
    """该事实类对应的 manifest 接入声明片段（写进源单元 manifest）。"""
    timing = timing_for(fact_class)
    return {
        "revision_policy": timing.revision_policy,
        "finalize_grace_days": timing.grace_days,
    }


def fact_address(fact_class: str, **keys: object) -> str:
    """跨进程/跨重启稳定的事实地址。

    地址由"事实类 + 该类的键"确定性拼出，不掺入运行上下文（进程号、内存序号、
    抓取时刻一律不进地址），因此同一事实在任何进程算出的地址相同。
    缺键即抛：宁可失败，也不要生成一个看起来合法、实际无法唯一定位的地址。
    """
    if fact_class == "catalog_entry":
        # 目录条目的键分两种形态（实测两种都必须，缺一个就撞地址）：
        #   1) 西药/中成药段：同一 item_no 有多个剂型（★ 变体），剂型是判别位；
        #   2) 谈判药品段：没有独立剂型列，但段内「西药/中成药/竞价」三套编号**各自
        #      从 1 开始**，所以必须带 subsection + pricing_channel，否则三个 #1 撞成一条。
        base_missing = [k for k in ("catalog_year", "section", "item_no")
                        if keys.get(k) in (None, "")]
        if base_missing:
            raise ValueError(f"fact_address({fact_class}) 缺键: {base_missing}")
        if keys.get("subsection"):
            # 谈判段：段内三套编号各自从 1 起，必须带分部+渠道
            disc = ("subsection", "pricing_channel")
            missing = [k for k in disc if keys.get(k) in (None, "")]
        else:
            # 西药/中成药段：同一编号下既有剂型变体（★），也有**同编号同剂型但不同
            # 盐基**的真不同品种（实测：山莨菪碱 / 消旋山莨菪碱 共用 39 号同一剂型），
            # 所以药名与剂型都是判别位。
            # 但剂型**允许为空**：凡例写明「名称中已含剂型的，剂型不单列」，实测 1695 行
            # 就是这么来的（如「注射用艾普拉唑钠」本身含剂型）。把剂型设成必填会把这些
            # 正常条目判成不可寻址。空值参与拼接，唯一性由数据侧实测保证。
            disc = ("drug_name", "dosage_form")
            # 两者至少有一个非空即可：只知道品种编号与"某剂型"时，编号+剂型仍能定位。
            missing = [] if (keys.get("drug_name") or keys.get("dosage_form")) else ["drug_name|dosage_form"]
        if missing:
            raise ValueError(f"fact_address({fact_class}) 缺键: {missing}")
        parts = ["catalog_entry", f"catalog_year={keys['catalog_year']}",
                 f"section={keys['section']}", f"item_no={keys['item_no']}"]
        for k in disc:
            parts.append(f"{k}={keys.get(k) or ''}")
        return "|".join(str(p) for p in parts)

    required = {
        "drug_approval": ("approval_no",),
        "clinical_trial": ("registry", "registry_id"),
    }[fact_class]
    missing = [k for k in required if keys.get(k) in (None, "")]
    if missing:
        raise ValueError(f"fact_address({fact_class}) 缺键: {missing}")
    parts = [fact_class] + [f"{k}={keys[k]}" for k in required]
    return "|".join(str(p) for p in parts)


def revision_record(
    *,
    address: str,
    old_value: object,
    new_value: object,
    source: str,
    observed_at: datetime,
) -> dict:
    """构造一条**修订**记录（保留旧值，不就地覆写）。

    与 R1 的三种合法实现一致：这里选"追加一条修订事实"，旧值随记录一起落库，
    回放时能回答"当前值是什么、之前是什么、谁改的"。
    """
    if not source:
        raise ValueError("revision_record 必须带 source（修订来源不得为空）")
    return {
        "address": address,
        "kind": "revision",
        "old_value": old_value,
        "new_value": new_value,
        "source": source,
        "observed_at": observed_at,
    }


def state_fact(
    *,
    address: str,
    state: str,
    source: str,
    observed_at: datetime,
) -> dict:
    """构造一条**状态事实**（状态推进追加新行，不改既有状态事实）。

    用于临床试验"进行中 → 已完成"这类推进：旧状态行保持原样，新状态作为独立事实
    追加，按时间回放能看到两次状态及其时点。
    """
    if not state:
        raise ValueError("state_fact 必须带 state")
    if not source:
        raise ValueError("state_fact 必须带 source")
    return {
        "address": address,
        "kind": "state",
        "state": state,
        "source": source,
        "observed_at": observed_at,
    }


def accepts_revision(
    fact_class: str,
    period: str,
    *,
    now: datetime | None = None,
) -> bool:
    """这一期的修订是否仍可入账（未定型）。作为写入侧的门。"""
    return not timing_for(fact_class).is_finalized(period, now=now)
