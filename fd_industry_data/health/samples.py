"""samples — golden 样本的加载与 schema 校验。

样本文件位于 ``spiders/<slug>/golden/<sample_id>.json``，格式见 guard
模块契约。``load_samples`` 只做发现与排序（按文件名），schema 校验交给
``validate_sample``，两者都返回/收集具体错误信息而不抛业务异常（非法
JSON 除外）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

__all__ = ["load_samples", "validate_sample"]

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load_samples(unit_dir: Path) -> list[dict]:
    """加载 ``<unit_dir>/golden/*.json``，按文件名排序；目录不存在→[]。"""
    golden_dir = Path(unit_dir) / "golden"
    if not golden_dir.is_dir():
        return []
    docs: list[dict] = []
    for path in sorted(golden_dir.glob("*.json"), key=lambda p: p.name):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load golden sample {path}: {exc}") from exc
        if not isinstance(doc, dict):
            raise ValueError(f"golden sample {path} must be a JSON object")
        docs.append(doc)
    return docs


def _check_type(errors: list[str], doc: dict, key: str, types: tuple[type, ...],
                type_name: str) -> Any:
    if key not in doc:
        errors.append(f"{key}: missing required field")
        return None
    value = doc[key]
    if isinstance(value, bool) and bool not in types:
        errors.append(f"{key}: expected {type_name}, got bool")
        return None
    if not isinstance(value, types):
        errors.append(f"{key}: expected {type_name}, got {type(value).__name__}")
        return None
    return value


def validate_sample(doc: dict) -> list[str]:
    """校验 golden 样本的必填项与类型，返回错误列表（空=合法）。

    必填：sample_id/target/created/params/expect/whitelist_fields；
    expect 至少含 min_rows / rows_contains 之一；
    sequence 可选，出现时校验 key_field/value_field/tolerance_pct。
    """
    errors: list[str] = []
    if not isinstance(doc, dict):
        return [f"sample must be a dict, got {type(doc).__name__}"]

    sample_id = _check_type(errors, doc, "sample_id", (str,), "str")
    if isinstance(sample_id, str) and not sample_id.strip():
        errors.append("sample_id: must be non-empty")

    target = _check_type(errors, doc, "target", (str,), "str")
    if isinstance(target, str) and not target.strip():
        errors.append("target: must be non-empty")

    created = _check_type(errors, doc, "created", (str,), "str")
    if isinstance(created, str) and not _DATE_RE.match(created):
        errors.append("created: expected YYYY-MM-DD")

    params = _check_type(errors, doc, "params", (dict,), "dict")

    expect = _check_type(errors, doc, "expect", (dict,), "dict")
    if isinstance(expect, dict):
        if not expect:
            errors.append("expect: must not be empty")
        if "min_rows" in expect:
            min_rows = expect["min_rows"]
            if isinstance(min_rows, bool) or not isinstance(min_rows, int):
                errors.append(
                    f"expect.min_rows: expected int, got {type(min_rows).__name__}"
                )
            elif min_rows < 0:
                errors.append(f"expect.min_rows: must be >= 0, got {min_rows}")
        if "rows_contains" in expect:
            rules = expect["rows_contains"]
            if not isinstance(rules, list):
                errors.append(
                    f"expect.rows_contains: expected list, got {type(rules).__name__}"
                )
            else:
                for i, rule in enumerate(rules):
                    if not isinstance(rule, dict):
                        errors.append(
                            f"expect.rows_contains[{i}]: expected dict, "
                            f"got {type(rule).__name__}"
                        )
                    elif "field" not in rule or "equals" not in rule:
                        errors.append(
                            f"expect.rows_contains[{i}]: need both field and equals"
                        )
                    elif not isinstance(rule["field"], str) or not rule["field"]:
                        errors.append(
                            f"expect.rows_contains[{i}].field: must be non-empty str"
                        )
        if "min_rows" not in expect and "rows_contains" not in expect:
            errors.append("expect: need at least one of min_rows / rows_contains")

    whitelist = _check_type(errors, doc, "whitelist_fields", (list,), "list")
    if isinstance(whitelist, list):
        for i, item in enumerate(whitelist):
            if not isinstance(item, str) or not item:
                errors.append(
                    f"whitelist_fields[{i}]: must be non-empty str, got {item!r}"
                )

    if "sequence" in doc:
        seq = doc["sequence"]
        if not isinstance(seq, dict):
            errors.append(
                f"sequence: expected dict, got {type(seq).__name__}"
            )
        else:
            for key in ("key_field", "value_field"):
                if not isinstance(seq.get(key), str) or not seq.get(key):
                    errors.append(f"sequence.{key}: must be non-empty str")
            if "tolerance_pct" in seq:
                tol = seq["tolerance_pct"]
                if isinstance(tol, bool) or not isinstance(tol, (int, float)):
                    errors.append(
                        f"sequence.tolerance_pct: expected number, "
                        f"got {type(tol).__name__}"
                    )
                elif tol < 0:
                    errors.append(
                        f"sequence.tolerance_pct: must be >= 0, got {tol}"
                    )

    return errors