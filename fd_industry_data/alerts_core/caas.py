"""CaaS 投递契约的 Python 读者（openspec: add-caas-delivery-contract）。

**为什么单独一个模块**：形态闭集与收件方能力表是全系统唯一权威，必须只有一份数据。
``caas-forms.json`` 是本包内的一份**逐字节副本**（与
``openspec/changes/add-caas-delivery-contract/caas-forms.json`` 同 sha256），由
``scripts/check_caas_conformance.py`` 校验一致——镜像各自独立构建，不能跨仓读文件。

本模块只提供**读表与判定**，不做网络、不写库，和 ``delivery.py`` 的分工相同：
口径离线可钉死，通道连通性另测。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

FORMS_PATH = Path(__file__).with_name("caas-forms.json")

_raw: dict[str, Any] | None = None


def load() -> dict[str, Any]:
    """读并缓存权威表。缓存是为了让 ``judge`` 这类热路径不反复读盘。"""
    global _raw
    if _raw is None:
        _raw = json.loads(FORMS_PATH.read_text(encoding="utf-8"))
    return _raw


def reset_cache() -> None:
    """测试用：强制重新读盘（改完 json 后不重启也能验）。"""
    global _raw
    _raw = None


def version() -> int:
    return int(load()["version"])


def forms() -> tuple[str, ...]:
    return tuple(load()["forms"].keys())


def ack_order() -> tuple[str, ...]:
    return tuple(load()["ack"]["order"])


def ack_aliases() -> dict[str, str]:
    return dict(load()["ack"].get("aliases") or {})


def canonical_ack(word: object) -> str | None:
    """把 ack 词归一到闭集值；``read``/``acted`` 等别名折到 ``consumed``。"""
    w = str(word or "").strip().lower()
    if not w:
        return None
    order = ack_order()
    if w in order:
        return w
    alias = ack_aliases().get(w)
    return alias if alias in order else None


def ack_rank(word: object) -> int:
    c = canonical_ack(word)
    return -1 if c is None else ack_order().index(c)


def caps_for(recipient_kind: str) -> tuple[str, ...]:
    row = (load().get("recipients") or {}).get(recipient_kind)
    return tuple(row.get("caps") or ()) if isinstance(row, Mapping) else ()


def max_ack_for(recipient_kind: str) -> str | None:
    row = (load().get("recipients") or {}).get(recipient_kind)
    return str(row.get("max_ack")) if isinstance(row, Mapping) and row.get("max_ack") else None


def channel_forms(channel: str) -> tuple[str, ...]:
    """一条投递渠道可承载的形态。

    渠道→形态的映射也在权威表里，不在代码里：否则每个消费方都会自己长出一份
    「relay 能发什么」的记忆，本能力就白定义了。
    """
    row = (load().get("channels") or {}).get(channel)
    return tuple(row.get("forms") or ()) if isinstance(row, Mapping) else ()


def channel_max_ack(channel: str) -> str | None:
    row = (load().get("channels") or {}).get(channel)
    return str(row.get("max_ack")) if isinstance(row, Mapping) and row.get("max_ack") else None


def resolve_form(available: Iterable[str], recipient_caps: Iterable[str]) -> str | None:
    """发送方按自己的偏好排序，收件方只做否决。无交集返回 ``None``。

    与 JS 侧 ``resolveForm`` 逐行等价：调用方拿到 ``None`` 必须失败并报出双方集合，
    **不得**静默降级成收件方从未声明的形态。
    """
    caps = set(recipient_caps)
    for f in available:
        if f in caps:
            return f
    return None


@dataclass(frozen=True)
class AckProjection:
    """一次投递归一后的三值结局。

    ``downgraded`` 是本能力的关键位：声明 ``read`` 而实际只证明 ``delivered`` 时，
    它必须为真——否则「没有报错」会被读成「契约已满足」。
    """

    state: str  # delivered | consumed | failed
    retryable: bool = False
    reason: str = ""
    downgraded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "retryable": self.retryable,
            "reason": self.reason,
            "downgraded": self.downgraded,
        }


def ack_projection(
    *,
    ack_declared: object = None,
    ack_actual: object = None,
    failed: bool = False,
    retryable: bool = False,
    reason: str = "",
) -> AckProjection:
    """把一次投递投影成三值之一。与 JS 侧 ``ackProjection`` 同语义。"""
    if failed:
        return AckProjection("failed", bool(retryable), str(reason or "delivery failed"), False)
    actual = canonical_ack(ack_actual)
    declared = canonical_ack(ack_declared)
    if actual is None:
        return AckProjection("failed", False, f"unknown ack actual: {ack_actual}", False)
    downgraded = declared is not None and ack_rank(actual) < ack_rank(declared)
    return AckProjection(actual, False, "", downgraded)


@dataclass(frozen=True)
class EnvelopeCheck:
    ok: bool
    missing: tuple[str, ...] = ()
    reason: str = ""


def validate_envelope(env: Mapping[str, Any] | None) -> EnvelopeCheck:
    """校验信封形状。缺字段要**逐个点名**，不得把投递降级成广播。"""
    if not isinstance(env, Mapping):
        return EnvelopeCheck(False, ("<envelope>",), "not a mapping")

    missing: list[str] = []
    to = env.get("to")
    has_to = bool(
        (isinstance(to, str) and to.strip())
        or (isinstance(to, Mapping) and any(to.get(k) for k in ("seatId", "seat_id", "slug", "email", "url")))
    )
    if not has_to:
        missing.append("to")

    raw_forms = env.get("forms")
    form_list = list(raw_forms) if isinstance(raw_forms, Sequence) and not isinstance(raw_forms, (str, bytes)) else []
    if not form_list:
        missing.append("forms")
    else:
        unknown = [f for f in form_list if f not in forms()]
        if unknown:
            return EnvelopeCheck(False, (), f"unknown form(s): {', '.join(map(str, unknown))}")

    if canonical_ack(env.get("ack")) is None:
        missing.append("ack")

    corr = env.get("correlation")
    if not isinstance(corr, Mapping) or not str(corr.get("idem") or "").strip():
        missing.append("correlation.idem")

    if missing:
        return EnvelopeCheck(False, tuple(missing), "missing required field(s)")
    return EnvelopeCheck(True)
