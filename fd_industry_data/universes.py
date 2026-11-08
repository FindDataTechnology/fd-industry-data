"""共享金融车道的宇宙注册表加载器。

唯一真源是仓库根的 ``universes/<region>.yaml``（见 ``universes/README.md``）。
车道单元 MUST 从这里取默认宇宙，MUST NOT 在源码内硬编码公司列表。

失败契约：注册表缺失、条目缺必填字段、ticker 重复、region 文件缺失 —— 一律抛
``UniverseRegistryError``。**不静默回落**到任何内置列表：车道默认宇宙的正确性
比可用性更重要（静默回落会让「没配」与「配了空」无法区分）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

REGISTRY_DIRNAME = "universes"
ENV_OVERRIDE = "FD_UNIVERSE_DIR"

# region -> 注册表文件名（文件名用小写，region 标识保持上游惯例的大写形态）
REGION_FILES: dict[str, str] = {
    "HK": "hk.yaml",
    "US": "us.yaml",
    "CN-A": "cn-a.yaml",
}

_REQUIRED_FIELDS = ("ticker", "name_zh", "segment", "verticals")


class UniverseRegistryError(RuntimeError):
    """宇宙注册表不可用或内容不合规。"""


@dataclass(frozen=True)
class Entity:
    """注册表中的一个监控实体。"""

    ticker: str
    name_zh: str
    name_en: str
    segment: str
    verticals: tuple[str, ...]


def registry_root() -> Path:
    """定位注册表目录。

    优先 ``FD_UNIVERSE_DIR``（测试与非常规部署用），否则从本文件向上找到第一个
    含 ``universes/`` 的祖先目录（仓库根或镜像 ``/app``）。
    """

    override = os.environ.get(ENV_OVERRIDE)
    if override:
        path = Path(override).expanduser().resolve()
        if not path.is_dir():
            raise UniverseRegistryError(
                f"{ENV_OVERRIDE}={override} is not a directory"
            )
        return path

    for parent in Path(__file__).resolve().parents:
        candidate = parent / REGISTRY_DIRNAME
        if candidate.is_dir():
            return candidate

    raise UniverseRegistryError(
        f"no {REGISTRY_DIRNAME}/ directory found above {Path(__file__).resolve()}; "
        f"set {ENV_OVERRIDE} or ship the registry with the image"
    )


@lru_cache(maxsize=8)
def load_region(region: str) -> tuple[Entity, ...]:
    """读取并校验一个 region 的全部实体（顺序即文件顺序）。"""

    filename = REGION_FILES.get(region)
    if filename is None:
        raise UniverseRegistryError(
            f"unknown region {region!r}; known: {sorted(REGION_FILES)}"
        )

    path = registry_root() / filename
    if not path.is_file():
        raise UniverseRegistryError(f"registry file missing: {path}")

    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - malformed yaml is a build break
        raise UniverseRegistryError(f"{path}: invalid YAML: {exc}") from exc

    declared_region = doc.get("region")
    if declared_region != region:
        raise UniverseRegistryError(
            f"{path}: declares region={declared_region!r}, expected {region!r}"
        )

    raw_entities = doc.get("entities") or []
    if not isinstance(raw_entities, list) or not raw_entities:
        raise UniverseRegistryError(f"{path}: 'entities' must be a non-empty list")

    entities: list[Entity] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_entities):
        if not isinstance(item, dict):
            raise UniverseRegistryError(f"{path}: entities[{index}] is not a mapping")
        missing = [f for f in _REQUIRED_FIELDS if not item.get(f)]
        if missing:
            raise UniverseRegistryError(
                f"{path}: entities[{index}] missing required field(s) {missing}"
            )
        ticker = str(item["ticker"]).strip()
        if not ticker:
            raise UniverseRegistryError(f"{path}: entities[{index}] has empty ticker")
        if ticker in seen:
            raise UniverseRegistryError(f"{path}: duplicate ticker {ticker}")
        seen.add(ticker)
        verticals = item["verticals"]
        if not isinstance(verticals, list) or not all(
            isinstance(v, str) and v for v in verticals
        ):
            raise UniverseRegistryError(
                f"{path}: entities[{index}] 'verticals' must be a non-empty string list"
            )
        entities.append(
            Entity(
                ticker=ticker,
                name_zh=str(item["name_zh"]).strip(),
                name_en=str(item.get("name_en") or "").strip(),
                segment=str(item["segment"]).strip(),
                verticals=tuple(verticals),
            )
        )

    return tuple(entities)


def region_tickers(region: str) -> tuple[str, ...]:
    """region 内全部 ticker（按注册表顺序）。"""

    return tuple(e.ticker for e in load_region(region))


def region_pairs(region: str) -> tuple[tuple[str, str], ...]:
    """region 内全部 ``(ticker, name_zh)``（按注册表顺序）。"""

    return tuple((e.ticker, e.name_zh) for e in load_region(region))


def tickers_for_vertical(region: str, vertical: str) -> tuple[str, ...]:
    """某个垂直线的 ticker 子集（多归属实体按 ``verticals`` 命中）。"""

    return tuple(e.ticker for e in load_region(region) if vertical in e.verticals)


def clear_cache() -> None:
    """测试用：清空 ``load_region`` 缓存。"""

    load_region.cache_clear()
