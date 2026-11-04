"""edu-catalog 五工具闭集（spec cap2 R1-R4；design D4 独立只读服务）。

工具面 = {edu_search, edu_item_detail, edu_overview, edu_subscribe, edu_unsubscribe}
- 前三只读；订阅两工具为唯一写面且 MUST 只产订阅记录、不改目录条目（R1 场景二）。
- 检索/详情只放行 publish_gate 通过行（cap1 R4「缺溯源不入发布态」消费点）。
- 空结果（no_match，注明检索域）与通道故障（reason 枚举）严格分离（cap2 R3）。
- 卡片层结果明示边界：fulltext_acquired=False + 原站外链 + 无正文（cap2 R2）。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from typing import Callable

from spiders.education_catalog_core import publish_gate

REASONS = ("upstream_unreachable", "timeout", "unauthorized", "stale")

CHANNEL_LIVE = "openalex-live"  # 实时论文通道标记（spec cap2 facade R；openalex_live.CHANNEL 同值）


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


class ChannelError(Exception):
    def __init__(self, channel: str, reason: str, detail: str = ""):
        assert reason in REASONS, f"unknown failure reason: {reason!r}"
        self.channel, self.reason, self.detail = channel, reason, detail
        super().__init__(f"{channel}: {reason}: {detail[:200]}")


def _row_channel(row: dict) -> str:
    return row.get("education_source") or row.get("source") or "catalog"


def _visible_rows(store, injected_error: ChannelError | None) -> tuple[list[dict], dict, dict]:
    """目录行读取 + 发布态闸过滤 + 撤回/合并墓碑传导（spec cap1 R5「上游修订与删除传导」）。

    墓碑行（deleted_check 产物：status ∈ withdrawn|merged）按 work_id/openalex_id/
    resource_id 命中存量条目：命中者退出现行态——检索 MUST NOT 返回，详情 MUST 呈现
    withdrawn/merged 标记态（含 merged_into 重定向），而非静默消失（cap1 R5 场景）。
    返回 (published, stats, tombstones)。
    """
    if injected_error is not None:
        raise injected_error
    rows = store.rows()
    tombstones: dict[str, dict] = {}
    for r in rows:
        if r.get("status") in ("withdrawn", "merged"):
            for k in filter(None, (r.get("work_id"), r.get("openalex_id"), r.get("resource_id"))):
                tombstones[str(k)] = r

    published, unpublished, withdrawn_hits = [], 0, 0
    for r in rows:
        if r.get("status") in ("withdrawn", "merged"):
            continue
        ok, _ = publish_gate(r)
        if not ok:
            unpublished += 1
            continue
        keys = filter(None, (r.get("work_id"), r.get("openalex_id"), r.get("resource_id")))
        if any(str(k) in tombstones for k in keys):
            withdrawn_hits += 1  # 现行态退场，标记态由 detail 呈现
            continue
        published.append(r)
    return published, {"total_ingested": len(rows), "unpublished_excluded": unpublished,
                       "withdrawn_or_merged_excluded": withdrawn_hits}, tombstones


def _card_boundary(row: dict) -> dict | None:
    """卡片层行加边界声明（cap2 R2「无许可状态不得出卡」+「必须明示正文在原站」）。"""
    if row.get("body_state") == "link_only" and row.get("fulltext_acquired") is False:
        return {"note": "正文在原站，本目录未取正文", "external_link": row.get("source_url")}
    return None


def register(mcp, *, catalog: Callable[[], "object"], subs, error_hook: Callable[[], ChannelError | None] | None = None,
             live_search: Callable[[str, int], list[dict]] | None = None):
    """装配五工具。catalog=目录存储访问器；subs=订阅存储；error_hook=测试注入通道故障。

    live_search=论文实时通道注入点（生产=``openalex_live.search_openalex`` 的 (query, limit)
    柯里化；测试可注入假通道）。注入 None=通道未装配（本地目录单机形态，不触网）。
    """

    def _err():
        return error_hook() if error_hook else None

    def _fail_envelope(exc: ChannelError) -> dict:
        return {
            "status": "channel_failure",
            "reason": exc.reason,
            "channel": exc.channel,
            "detail": exc.detail[:200],
            "as_of": utc_now_iso(),
        }

    def edu_search(query: str = "", resource_type: str = "", subject: str = "",
                   platform: str = "", creator: str = "", limit: int = 20) -> dict:
        """目录检索：跨论文/播客/课程/卡片；过滤后带许可层与新鲜度。

        论文通道实时优先（spec cap2 facade R）：装配 live_search 且带 query 时先查
        OpenAlex 实时通道，故障显式报因并回落本地目录（回落事实可见）。
        """
        def _local_search(rows: list[dict], gate_stats: dict) -> list[dict]:
            hits = []
            for r in rows:
                if resource_type and (r.get("resource_type") or _infer_type(r)) != resource_type:
                    continue
                if subject and subject.lower() not in json.dumps(r.get("primary_topic_display_name") or r.get("title") or "", ensure_ascii=False).lower():
                    continue
                if platform and platform.lower() not in str(r.get("platform") or "").lower():
                    continue
                if creator and creator.lower() not in json.dumps(r.get("creators") or r.get("authors") or "", ensure_ascii=False).lower():
                    continue
                if q:
                    hay = " ".join(str(r.get(k) or "") for k in ("title", "display_name", "org", "notes")).lower()
                    if q not in hay:
                        continue
                item = {
                    "resource_id": r.get("resource_id"),
                    "title": r.get("title") or r.get("display_name"),
                    "resource_type": _infer_type(r),
                    "platform": r.get("platform") or _row_channel(r),
                    "license": r.get("license"),
                    "license_layer": r.get("license_layer") or r.get("license"),
                    "commercial_ok": r.get("commercial_ok", True),
                    "body_state": r.get("body_state"),
                    "as_of": r.get("scraped_at"),
                    "source_url": r.get("source_url"),
                }
                cb = _card_boundary(r)
                if cb:
                    item["card_boundary"] = cb
                hits.append(item)
                if len(hits) >= limit:
                    break
            return hits

        try:
            rows, gate_stats, _tombs = _visible_rows(catalog(), _err())
        except ChannelError as e:
            return _fail_envelope(e)
        q = (query or "").lower()
        # ── 论文实时通道（spec cap2「实时优先/本地回落」，2026-11-04 晚纠偏）──
        # 触发：装配了 live_search 且是论文检索（query 非空、resource_type 未排除 paper）。
        # 故障 → 显式报因 + 回落本地（回落事实 MUST 可见，MUST NOT 伪装无匹配）。
        live_error: dict | None = None
        live_hits: list[dict] = []
        if live_search is not None and q and resource_type in ("", "paper"):
            try:
                live_hits = live_search(query, limit)
            except ChannelError as e:
                live_error = _fail_envelope(e)

        hits: list[dict] = [{**it, "channel": CHANNEL_LIVE} for it in live_hits]  # 通道标记查询面保证
        hits += _local_search(rows, gate_stats)
        hits = hits[:limit] if live_hits else hits  # 实时在场时本地只补缺

        if not hits:
            envelope = {"status": "no_match",
                        "searched": {"query": query, "resource_type": resource_type,
                                     "subject": subject, "platform": platform, "creator": creator},
                        "catalog_size": gate_stats["total_ingested"],
                        "as_of": utc_now_iso()}
            if live_error is not None:
                envelope["live_channel"] = live_error  # 回落事实可见（reason/channel/as_of）
            return envelope
        result = {"status": "ok", "items": hits, "catalog": gate_stats, "as_of": utc_now_iso()}
        if live_hits:
            result["channels"] = {"live": len(live_hits), "local": max(len(hits) - len(live_hits), 0)}
        elif live_error is not None:
            result["live_channel"] = live_error  # 本地命中但实时故障——降级事实可见
        return result

    def edu_item_detail(resource_id: str) -> dict:
        """条目详情：正文可达层返回提取文本；卡层返回外链+授权状态。
        未发布/不存在→显式原因；命中撤回/合并墓碑→呈现标记态（cap1 R5 重定向）。"""
        try:
            rows, _, tombs = _visible_rows(catalog(), _err())
        except ChannelError as e:
            return _fail_envelope(e)
        if resource_id in tombs:
            t = tombs[resource_id]
            marked = {"resource_id": resource_id,
                      "marked_state": t.get("status"),
                      "as_of": t.get("scraped_at")}
            if t.get("merged_into"):
                marked["redirect_to"] = t["merged_into"]
            return {"status": "ok", "item": marked, "note": "entry retired from current state (spec cap1 R5)"}
        for r in rows:
            if r.get("resource_id") == resource_id or r.get("work_id") == resource_id:
                detail = dict(r)
                if r.get("body_state") == "link_only":
                    detail["card_boundary"] = {"note": "正文在原站，本目录未取正文",
                                               "external_link": r.get("source_url")}
                detail["as_of"] = r.get("scraped_at")
                return {"status": "ok", "item": detail}
        # 发布态闸外还是真不存在？（spec cap1 R4：缺溯源字段者不可见——给可判别原因）
        all_rows = catalog().rows() if not _err() else []
        for r in all_rows:
            if (r.get("resource_id") == resource_id or r.get("work_id") == resource_id) and not publish_gate(r)[0]:
                missing = publish_gate(r)[1]
                return {"status": "not_indexed",
                        "reason": f"entry exists but unpublished (missing {missing})",
                        "as_of": utc_now_iso()}
        return {"status": "not_indexed", "reason": "resource not in catalog", "as_of": utc_now_iso()}

    def edu_overview() -> dict:
        """目录概况：各许可层条目量+逐通道健康（as-of）+撤回计数。"""
        try:
            rows, gate_stats, _tombs = _visible_rows(catalog(), _err())
        except ChannelError as e:
            return _fail_envelope(e)
        by_layer: dict[str, int] = {}
        by_channel: dict[str, dict] = {}
        for r in rows:
            layer = str(r.get("license_layer") or r.get("license") or "unknown")
            by_layer[layer] = by_layer.get(layer, 0) + 1
            ch = _row_channel(r)
            h = by_channel.setdefault(ch, {"count": 0, "latest_as_of": None})
            h["count"] += 1
            ts = r.get("scraped_at")
            if ts and (h["latest_as_of"] is None or ts > h["latest_as_of"]):
                h["latest_as_of"] = ts
        return {"status": "ok", "published_by_license_layer": by_layer,
                "by_channel": by_channel, "catalog": gate_stats, "as_of": utc_now_iso()}

    def edu_subscribe(kind: str, target: str, cadence_min: int = 1440) -> dict:
        """订阅建立（唯一写面之一）：kind ∈ topic|creator|source；只产订阅记录。"""
        if kind not in ("topic", "creator", "source"):
            return {"status": "rejected", "reason": f"kind must be topic|creator|source, got {kind!r}"}
        if not target:
            return {"status": "rejected", "reason": "target required"}
        sub_id = hashlib.sha1(f"{kind}:{target}".encode()).hexdigest()[:12]
        record = {"id": sub_id, "kind": kind, "target": target,
                  "cadence_min": cadence_min, "created_at": utc_now_iso()}
        subs.add(record)
        return {"status": "ok", "subscription": record, "as_of": utc_now_iso()}

    def edu_unsubscribe(subscription_id: str) -> dict:
        """订阅取消（唯一写面之二）：只删订阅记录。"""
        removed = subs.remove(subscription_id)
        if not removed:
            return {"status": "not_indexed", "reason": f"subscription {subscription_id!r} not found",
                    "as_of": utc_now_iso()}
        return {"status": "ok", "removed": subscription_id, "as_of": utc_now_iso()}

    for fn in (edu_search, edu_item_detail, edu_overview, edu_subscribe, edu_unsubscribe):
        mcp.tool(fn)

    return {"edu_search", "edu_item_detail", "edu_overview", "edu_subscribe", "edu_unsubscribe"}


def _infer_type(row: dict) -> str:
    rid = str(row.get("resource_id") or "")
    es = str(row.get("education_source") or "")
    if ".paper." in rid or es in ("arxiv_new", "epmc_oa") or rid.startswith("https://openalex.org/"):
        return "paper"
    if es == "podcast_rss" or rid.startswith("edu.episode"):
        return "podcast_episode"
    if es == "apple_seed":
        return "podcast_show"
    if es in ("ocw_sitemap", "openedx_catalog"):
        return "course"
    if es in ("gutenberg_catalog", "wikibooks_search", "doab_oai"):
        return "book"
    if es == "card_curated":
        return "card"
    if es in ("subfield_3304", "topic_T13106", "topic_T11875", "topic_T12171", "openalex"):
        return "paper"
    return "resource"
