"""行业线告警的公共内核：状态机、留痕、通知。

**为什么先抽内核**：`fd-industry-data` 下已有三份同类脚本
（`agriculture_alerts.py` / `flower_alerts.py` / `restaurant_alerts.py`），语义已经分叉——
冷却窗分别是「按周期」「恢复后小时」「没有冷却窗」，通知分别是「邮件」「无」「无」。
银行线如果照抄，就是第四份。本节只抽三份副本里**真正重复且真出问题**的三件事：
状态机、幂等留痕、通知隔离。判据求值器各线自有（阈值带/分位/YoY/z-score 差异太大，硬合并反而危险）。

模块：
  ``state``  —— 阈值状态机（quiet/firing/resolved）+ 事件规则的幂等去重
  ``ledger`` —— JSONL 事件留痕（按 (rule_id, kind, key) 幂等）与状态文件读写
  ``notify`` —— 邮件通知，单条失败不阻塞同批
"""
from __future__ import annotations

from .ledger import (
    append_events,
    event_key,
    load_state,
    read_events,
    save_state,
    state_health,
)
from .notify import SmtpConfig, dispatch, send_email, smtp_config_from_env
from .state import (
    FIRING,
    FIRING_V,
    INSUFFICIENT,
    OK,
    PENDING,
    QUIET,
    RESOLVED,
    RuleState,
    attach_rule,
    compare,
    evaluate_event,
    evaluate_threshold,
    make_event,
)

__all__ = [
    "FIRING", "FIRING_V", "QUIET", "RESOLVED", "OK", "INSUFFICIENT", "PENDING",
    "RuleState", "compare", "evaluate_event", "evaluate_threshold", "make_event",
    "attach_rule",
    "append_events", "read_events", "event_key", "load_state", "save_state",
    "state_health",
    "SmtpConfig", "dispatch", "send_email", "smtp_config_from_env",
]
