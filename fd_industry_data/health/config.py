"""自愈闭环配置：默认值 + JSON 覆盖。

v1：配置来源为内置默认值，可经 FD_HEALTH_CONFIG 指向 JSON 文件覆盖。
中央库配置表 + Console 设置页（任务 2.1）落地后，本模块增加 DB 读取源，
字段名保持不变。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from .triage import Thresholds

# manifest frequency 字符串 -> 期望周期（小时）；on_demand/未知 返回 None（不参与新鲜度判定）
FREQUENCY_HOURS = {
    "hourly": 1.0,
    "15min": 0.25,
    "30min": 0.5,
    "daily": 24.0,
    "weekly": 168.0,
    "monthly": 720.0,
    "quarterly": 2160.0,
    "yearly": 8760.0,
    "annual": 8760.0,
}


def frequency_hours(frequency: str | None) -> float | None:
    if not frequency:
        return None
    return FREQUENCY_HOURS.get(str(frequency).strip().lower())


@dataclass(frozen=True)
class HealthConfig:
    thresholds: Thresholds = field(default_factory=Thresholds)
    # 每日最多派发的工单数（超出顺延次日；预检+当日已出单合并计数）
    max_daily_tickets: int = 5
    # 总闸：False = 纯巡检（照常出单，但不 SUBMIT 给修复执行体）
    master_switch: bool = True
    # 巡检回看窗口（小时）
    lookback_hours: float = 720.0
    # 遥测读取 SQL（中央库 crawl_runs；列名=运维实况 2026-10-04 核验：
    # error_head 为错误摘要列，无独立 http_status 列——http 状态从 error 文本判定）
    telemetry_sql: str = (
        "SELECT source, status, started_at, COALESCE(rows_written,0), "
        "COALESCE(error_head,''), '' "
        "FROM crawl_runs WHERE started_at >= now() - interval '30 days' "
        "ORDER BY source, started_at"
    )
    # 单源周期覆盖（source -> 小时）；缺省按 manifest.frequency 推断
    expected_period_overrides: dict = field(default_factory=dict)
    # 巡检排除的源（如接入演练中的临时源）
    excluded_sources: tuple = ()


DEFAULTS = HealthConfig()


def load_config(path: str | Path | None = None) -> HealthConfig:
    """默认值 + JSON 覆盖。path 缺省取环境变量 FD_HEALTH_CONFIG。"""
    p = Path(path) if path else None
    if p is None:
        env = os.environ.get("FD_HEALTH_CONFIG", "").strip()
        p = Path(env) if env else None
    if p is None or not p.exists():
        return DEFAULTS
    doc = json.loads(p.read_text(encoding="utf-8"))
    overrides: dict = {}
    if "thresholds" in doc:
        overrides["thresholds"] = Thresholds(**doc["thresholds"])
    for key in ("max_daily_tickets", "master_switch", "lookback_hours", "telemetry_sql"):
        if key in doc:
            overrides[key] = doc[key]
    if "expected_period_overrides" in doc:
        overrides["expected_period_overrides"] = {
            str(k): float(v) for k, v in doc["expected_period_overrides"].items()
        }
    if "excluded_sources" in doc:
        overrides["excluded_sources"] = tuple(doc["excluded_sources"])
    return replace(DEFAULTS, **overrides)