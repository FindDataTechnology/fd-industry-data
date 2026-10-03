"""自愈闭环配置：默认值 + JSON 文件覆盖 + 中央库 health_config 表。

优先级（由调用方组装）：--config/FD_HEALTH_CONFIG 文件 > 中央库 `public.health_config`
表（spider-self-heal-l2 任务 2.1 中央库形态；Console 设置页待后续）> 内置默认值。
中央库读取经 `psql` 子进程（与遥测同路径，纯 stdlib），不可达/空表时返回 None 由调用方回退。
"""
from __future__ import annotations

import json
import os
import subprocess
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

_KNOWN_KEYS = (
    "max_daily_tickets",
    "master_switch",
    "lookback_hours",
    "telemetry_sql",
    "expected_period_overrides",
    "excluded_sources",
)


def _config_from_doc(doc: dict) -> HealthConfig:
    """把 {key: value} 字典（文件或中央库行）合并进默认值；未知键忽略。"""
    overrides: dict = {}
    if "thresholds" in doc:
        overrides["thresholds"] = Thresholds(**doc["thresholds"])
    for key in _KNOWN_KEYS:
        if key not in doc:
            continue
        value = doc[key]
        if key == "expected_period_overrides":
            value = {str(k): float(v) for k, v in (value or {}).items()}
        elif key == "excluded_sources":
            value = tuple(value or [])
        overrides[key] = value
    return replace(DEFAULTS, **overrides)


def load_config(path: str | Path | None = None) -> HealthConfig:
    """默认值 + JSON 文件覆盖。path 缺省取环境变量 FD_HEALTH_CONFIG。"""
    p = Path(path) if path else None
    if p is None:
        env = os.environ.get("FD_HEALTH_CONFIG", "").strip()
        p = Path(env) if env else None
    if p is None or not p.exists():
        return DEFAULTS
    doc = json.loads(p.read_text(encoding="utf-8"))
    return _config_from_doc(doc)


def load_config_from_db(dsn: str, binary: str = "psql", timeout: int = 30) -> HealthConfig | None:
    """读中央库 `public.health_config` 表（key/value jsonb）；不可达/空表返回 None。

    纯只读；供巡检器与萬星 spider-heal 的总闸/限流读取（坐标见 docs/health-loop.md）。
    """
    if not dsn:
        return None
    try:
        proc = subprocess.run(
            [binary, dsn, "-At", "-F", "\t", "-c",
             "SELECT key, value FROM public.health_config"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    doc: dict = {}
    known = {"thresholds", *_KNOWN_KEYS}
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        key, _, raw = line.partition("\t")
        key = key.strip()
        if key not in known:
            continue
        try:
            doc[key] = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not doc:
        return None
    return _config_from_doc(doc)