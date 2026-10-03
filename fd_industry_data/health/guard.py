"""guard — golden 重放与序列一致性（口径守卫）。

纯 stdlib 实现，fetcher 可注入：单测与离线环境可用注入的 ``call_entry``
驱动，不触碰网络。

契约（与 spider-heal Agent Service 对齐）：

- ``load_unit_entry(unit_dir, target)``: 从 ``spiders/<slug>/spider.py`` 按
  文件路径加载模块（模块名 ``unit_<slug_with_underscores>``，避免
  ``sys.modules`` 冲突），返回 ``target`` 指定的入口函数。
- ``replay_sample(unit_dir, sample, call_entry=None)``: 按 golden 样本重放
  入口函数，校验 ``expect.min_rows`` / ``expect.rows_contains``；任何异常
  都收敛为 ``ok=False``，异常摘要写入 ``diffs``。
- ``check_sequence(sample, rows, reference, tolerance_pct=None)``: 序列口径
  校验（v1 取最后一行含 key/value 字段的行与 reference 值比对相对差）。
- ``classify_failure(replay_ok, upstream_changed)``: 确定性分诊，映射到
  ``ok`` / ``sample-update-pending-human`` / ``repair-failed``。
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "ReplayResult",
    "SequenceResult",
    "load_unit_entry",
    "replay_sample",
    "check_sequence",
    "classify_failure",
]

DEFAULT_TOLERANCE_PCT = 1.0


@dataclass
class ReplayResult:
    """一次 golden 重放的结果。``diffs`` 为空表示全部期望命中。"""

    ok: bool
    diffs: list[str]
    rows: int
    sample_id: str


@dataclass
class SequenceResult:
    """序列口径校验结果。``skipped=True`` 表示未做比对（缺 reference 等）。"""

    ok: bool
    skipped: bool
    note: str


def load_unit_entry(unit_dir: Path, target: str) -> Callable[..., list[dict]]:
    """按路径加载 ``spiders/<slug>/spider.py`` 并取出 ``target`` 入口函数。

    模块名固定为 ``unit_<dir_name 连字符转下划线>``，避免与真实包名冲突；
    同一进程内重复加载会以新模块覆盖该键，保证拿到最新代码。
    """
    unit_dir = Path(unit_dir)
    spider_path = unit_dir / "spider.py"
    if not spider_path.is_file():
        raise FileNotFoundError(f"no spider.py under unit dir: {unit_dir}")

    module_name = f"unit_{unit_dir.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, spider_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot create import spec for {spider_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise

    entry = getattr(module, target, None)
    if entry is None:
        raise AttributeError(f"{spider_path} has no entry {target!r}")
    if not callable(entry):
        raise TypeError(f"entry {target!r} in {spider_path} is not callable")
    return entry


def _stringified_equals(actual: Any, expected: Any) -> bool:
    """golden 语义：字符串化比较（1 == "1", 1.0 == "1.0"）。"""
    return str(actual) == str(expected)


def _check_rows_contains(rows: list[Any], rules: Any) -> list[str]:
    diffs: list[str] = []
    if not isinstance(rules, list):
        return [f"expect.rows_contains must be a list, got {type(rules).__name__}"]
    for rule in rules:
        if not isinstance(rule, dict) or "field" not in rule or "equals" not in rule:
            diffs.append(f"rows_contains rule malformed (need field/equals): {rule!r}")
            continue
        field_name = rule["field"]
        expected = rule["equals"]
        hit = any(
            isinstance(row, dict)
            and field_name in row
            and _stringified_equals(row[field_name], expected)
            for row in rows
        )
        if not hit:
            diffs.append(
                f"rows_contains: no row with {field_name} == {expected!r}"
            )
    return diffs


def replay_sample(
    unit_dir: Path,
    sample: dict,
    call_entry: Callable[[str, dict], list[dict]] | None = None,
) -> ReplayResult:
    """重放 golden 样本并校验 ``expect``。

    ``call_entry`` 可注入，签名 ``(target, params) -> list[dict]``；缺省时
    用 ``load_unit_entry(unit_dir, target)(**params)`` 真实调用单元入口。
    入口抛出的任何异常都被收敛为 ``ok=False``（异常摘要进 ``diffs``）。
    """
    sample_id = str(sample.get("sample_id", ""))
    target = str(sample.get("target", ""))
    params = sample.get("params") or {}
    expect = sample.get("expect") or {}

    if not isinstance(params, dict):
        return ReplayResult(False, [f"params must be a dict, got {type(params).__name__}"],
                            0, sample_id)
    if not isinstance(expect, dict):
        return ReplayResult(False, [f"expect must be a dict, got {type(expect).__name__}"],
                            0, sample_id)
    if not target:
        return ReplayResult(False, ["sample has no target"], 0, sample_id)

    try:
        if call_entry is None:
            entry = load_unit_entry(unit_dir, target)
            rows = entry(**params)
        else:
            rows = call_entry(target, params)
    except Exception as exc:  # noqa: BLE001 - 契约：异常收敛为 ok=False
        return ReplayResult(
            ok=False,
            diffs=[f"entry raised {type(exc).__name__}: {exc}"],
            rows=0,
            sample_id=sample_id,
        )

    if not isinstance(rows, list):
        return ReplayResult(
            ok=False,
            diffs=[f"entry returned {type(rows).__name__}, expected list[dict]"],
            rows=0,
            sample_id=sample_id,
        )

    diffs: list[str] = []

    min_rows = expect.get("min_rows")
    if min_rows is not None:
        if isinstance(min_rows, bool) or not isinstance(min_rows, int):
            diffs.append(f"expect.min_rows must be an int, got {min_rows!r}")
        elif len(rows) < min_rows:
            diffs.append(f"min_rows: got {len(rows)} < expected {min_rows}")

    if "rows_contains" in expect:
        diffs.extend(_check_rows_contains(rows, expect["rows_contains"]))

    return ReplayResult(ok=not diffs, diffs=diffs, rows=len(rows), sample_id=sample_id)


def check_sequence(
    sample: dict,
    rows: list[dict],
    reference: dict | None,
    tolerance_pct: float | None = None,
) -> SequenceResult:
    """序列口径校验：最近一行 ``value_field`` 与参考值的相对差是否在容差内。

    - sample 无 ``sequence`` 段或 ``reference is None`` → ``skipped=True, ok=True``；
    - v1 取 rows 中最后一行同时含 ``key_field``/``value_field`` 的行；
    - 容差优先级：显式 ``tolerance_pct`` > 样本 ``sequence.tolerance_pct``
      > ``DEFAULT_TOLERANCE_PCT``；
    - 找不到可比对的行/值或非数值 → ``skipped=True, ok=False``（未验证成功，
      不静默通过）。
    """
    seq = sample.get("sequence")
    if not isinstance(seq, dict) or not seq:
        return SequenceResult(ok=True, skipped=True, note="sample has no sequence section")
    if reference is None:
        return SequenceResult(ok=True, skipped=True, note="no reference provided")

    key_field = seq.get("key_field")
    value_field = seq.get("value_field")
    if not key_field or not value_field:
        return SequenceResult(
            ok=False, skipped=True,
            note="sequence missing key_field/value_field",
        )

    ref_raw = reference.get("value")
    if isinstance(ref_raw, bool) or ref_raw is None:
        return SequenceResult(
            ok=False, skipped=False,
            note=f"reference value is not numeric: {ref_raw!r}",
        )
    try:
        ref_value = float(ref_raw)
    except (TypeError, ValueError):
        return SequenceResult(
            ok=False, skipped=False,
            note=f"reference value is not numeric: {ref_raw!r}",
        )

    match: dict | None = None
    for row in rows:
        if isinstance(row, dict) and key_field in row and value_field in row:
            match = row  # v1: 取最后一行
    if match is None:
        return SequenceResult(
            ok=False, skipped=True,
            note=f"no row carrying {key_field!r}/{value_field!r}",
        )

    actual_raw = match[value_field]
    if isinstance(actual_raw, bool):
        return SequenceResult(
            ok=False, skipped=False,
            note=f"value {actual_raw!r} for {value_field!r} is not numeric",
        )
    try:
        actual_value = float(actual_raw)
    except (TypeError, ValueError):
        return SequenceResult(
            ok=False, skipped=False,
            note=f"value {actual_raw!r} for {value_field!r} is not numeric",
        )

    if tolerance_pct is not None:
        tolerance = float(tolerance_pct)
    else:
        raw_tol = seq.get("tolerance_pct", DEFAULT_TOLERANCE_PCT)
        try:
            tolerance = float(raw_tol)
        except (TypeError, ValueError):
            return SequenceResult(
                ok=False, skipped=True,
                note=f"sequence.tolerance_pct is not numeric: {raw_tol!r}",
            )

    if ref_value == 0:
        diff_pct = 0.0 if actual_value == 0 else float("inf")
    else:
        diff_pct = abs(actual_value - ref_value) / abs(ref_value) * 100.0

    period = reference.get("period")
    note = (
        f"{key_field}={match.get(key_field)!r}"
        + (f" period={period!r}" if period is not None else "")
        + f": {value_field}={actual_value:g} vs reference={ref_value:g}, "
        f"diff={diff_pct:.4g}% tolerance={tolerance:g}%"
    )
    return SequenceResult(ok=diff_pct <= tolerance, skipped=False, note=note)


def classify_failure(replay_ok: bool, upstream_changed: bool) -> str:
    """确定性分诊：重放通过→ok；上游变而重放仍失败→待人工更新样本；否则修复失败。"""
    if replay_ok:
        return "ok"
    if upstream_changed:
        return "sample-update-pending-human"
    return "repair-failed"