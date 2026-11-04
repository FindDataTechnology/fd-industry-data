"""教育垂域实体登记与发布态闸（design D3；spec cap1 R3 三类实体 / R4 溯源 / 发布态）。

纯函数模块（零网络零库连接）——ingest 层落库与 7.x 查询面共同消费的判定件：

- ``make_code(kind, native_code)``：编码 ``edu.<kind>.<native_code>``（yearbook 配方换域）。
  kind ∈ resource/creator/platform。与 papers 的 OpenAlex 命名空间**对账不共用行**：
  同一篇论文在两域各自有行，以 DOI 等外部标识关联（``linkage_keys``）。
- ``dedupe_keys(row)``：合并三键 = 归一标题 + 创作者 + 外部标识（DOI>ISBN>feedUrl+guid>
  work_id>source_url）。**宁挂双条不错合**：外部标识一边有一边缺 → 不合并。
- ``group_presentations(rows)``：同资源实体的多平台呈现合并到一实体下，各呈现保留
  platform/license/body_state/as_of（spec cap1 R3 场景「同名跨平台资源合并」）。
- ``publish_gate(row)``：四字段缺一即未发布态（source_url/source/scraped_at/license）；
  返回 (ok, reasons)。查询面 MUST 只放行 ok=True 行（cap1 R4 场景「缺溯源字段不入
  发布态」的机制真源；6.2 端到端在 7.x 查询面消费本函数）。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

ENTITY_KINDS = ("resource", "creator", "platform")
PUBLISH_REQUIRED = ("source_url", "source", "scraped_at", "license")

_WS = re.compile(r"\s+")
_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


def make_code(kind: str, native_code: str) -> str:
    if kind not in ENTITY_KINDS:
        raise ValueError(f"entity kind must be one of {ENTITY_KINDS}, got {kind!r}")
    if not native_code:
        raise ValueError("native_code required")
    return f"edu.{kind}.{native_code}"


def normalize_title(title: str | None) -> str:
    s = _WS.sub(" ", (title or "").strip().lower())
    s = re.sub(r"[‘’'`\"“”:;,!?.]+$", "", s)
    return s


def normalize_creators(creators: Any) -> tuple[str, ...]:
    """接受 list[str] / JSON 字符串（authorships）/ None → 归一创作者元组（排序去重）。"""
    if not creators:
        return ()
    if isinstance(creators, str):
        import json
        try:
            parsed = json.loads(creators)
        except Exception:
            parsed = creators
        creators = parsed
    names: list[str] = []
    if isinstance(creators, dict):
        creators = creators.get("authors") or creators.get("creator") or []
    for c in creators if isinstance(creators, list) else [creators]:
        if isinstance(c, dict):
            n = c.get("display_name") or c.get("name") or c.get("raw_author_name")
        else:
            n = str(c)
        if n:
            names.append(_WS.sub(" ", n.strip().lower()))
    return tuple(sorted(set(names)))


def linkage_keys(row: dict) -> dict:
    """外部标识（跨域对账用，papers 命名空间与 edu 命名空间靠这些关联而非共用行）。"""
    out: dict[str, str] = {}
    doi = row.get("doi")
    if doi:
        out["doi"] = str(doi).lower().replace("https://doi.org/", "").strip()
    for key, field in (("isbn", "isbn"), ("feedurl", "feed_url")):
        v = row.get(field)
        if v:
            out[key] = str(v).strip()
    oid = row.get("openalex_id") or row.get("work_id") or ""
    if oid:
        out["work"] = str(oid)
    return out


def external_identity(row: dict) -> str | None:
    """合并三键的第三键：强外部标识优先；无强标识返回 None（→ 宁挂双条）。"""
    lk = linkage_keys(row)
    for k in ("doi", "isbn", "feedurl"):
        if k in lk:
            return f"{k}:{lk[k]}"
    return None


def dedupe_keys(row: dict) -> tuple:
    """合并三键。第三键缺位 ⇒ 唯一自键（永不与他人合并）。"""
    ext = external_identity(row)
    if ext is None:
        return ("self", row.get("resource_id") or row.get("source_url") or id(row))
    return ("merge", normalize_title(row.get("title") or row.get("display_name")),
            normalize_creators(row.get("creators") or row.get("authors")), ext)


def _merge_compatible(keys_a: tuple, keys_b: tuple) -> bool:
    if keys_a[0] == "self" or keys_b[0] == "self":
        return False
    return keys_a[1] == keys_b[1] and keys_a[2] == keys_b[2] and keys_a[3] == keys_b[3]


def group_presentations(rows: Iterable[dict]) -> list[dict]:
    """同实体多平台呈现聚合（spec cap1 R3）。返回实体行列表。"""
    entities: list[dict] = []
    for row in rows:
        k = dedupe_keys(row)
        placed = False
        for ent in entities:
            if _merge_compatible(ent["keys"], k):
                ent["presentations"].append({
                    "platform": row.get("platform") or row.get("source"),
                    "source_url": row.get("source_url"),
                    "license": row.get("license"),
                    "license_layer": row.get("license_layer"),
                    "commercial_ok": row.get("commercial_ok"),
                    "body_state": row.get("body_state"),
                    "as_of": row.get("scraped_at") or row.get("as_of"),
                })
                placed = True
                break
        if not placed:
            code = make_code("resource", row.get("resource_id") or row.get("work_id") or f"anon{len(entities)}")
            entities.append({
                "code": code,
                "title": row.get("title") or row.get("display_name"),
                "keys": k,
                "linkage": linkage_keys(row),
                "presentations": [{
                    "platform": row.get("platform") or row.get("source"),
                    "source_url": row.get("source_url"),
                    "license": row.get("license"),
                    "license_layer": row.get("license_layer"),
                    "commercial_ok": row.get("commercial_ok"),
                    "body_state": row.get("body_state"),
                    "as_of": row.get("scraped_at") or row.get("as_of"),
                }],
            })
    for e in entities:
        e.pop("keys")
    return entities


def publish_gate(row: dict) -> tuple[bool, list[str]]:
    """发布态四字段闸（spec cap1 R4）：缺一即未发布态，查询面不可见。"""
    reasons = [f for f in PUBLISH_REQUIRED if not row.get(f)]
    return (not reasons, reasons)
