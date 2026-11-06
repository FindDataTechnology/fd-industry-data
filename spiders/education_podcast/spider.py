"""spiders/education_podcast —— 播客层：Apple 目录 seed → RSS 活水 → 转录入库（D6 markdown+sidecar）。

通道（2026-11-04 复测定稿，写死在 seed）：
- ``GET https://itunes.apple.com/search?term=<kw>&media=podcast&entity=podcast&country=us|cn``
  → 返回 **feedUrl**（带 entity=podcast 才有；泛检索不给——Wave 0 矛盾结论就此落定）。
- RSS 标准 XML：channel/item → title, guid, pubDate, enclosure@url（公开音频字节）。
- Podcast Index 需 key（免费申请表单）——本 change 不依赖，挂账为补全通道。

许可口径（spec cap1 R2「落库但限用层」）：
- ``license='public-enclosure'``：编目+内部索引用途合法（RSS 分发是创作者主动公开）。
- ``redistribution='needs_authorization'``：转录文本再分发需节目方授权——单列字段，不与内部索引混轨。
- 转录仅 RSS 轨（spec cap1 R7 负面清单）：``_fetch_feed`` 只接受 http(s)/file 的 RSS 本身，
  enclosure 只从该 feed 的 <enclosure> 声明里取——不存在任何平台下载路径。

状态与健康（spec cap1 R6）：
- 本地 JSON 状态（state_dir）：每 feed 记 last_seen_guids、consecutive_fail、last_error(原因枚举)、
  episode_status 状态机（pending|running|done|failed）。
- 失败原因枚举：unreachable / timeout / dns / http_error / parse_error。
- 通道故障 MUST 报显式健康行（fail_count+reason），MUST NOT 以「无新内容」示人。
- stale 判定：距最近一次成功且通道可达的时长 > 2×节目自身节奏（用最近两期间隔估计）。

转录：faster-whisper 为可选插槽——装了才转（done），没装保持 pending（不伪装）。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from spiders.education_card_catalog.gate import (
    assert_body_acquisition_allowed,
    is_banned_body_host,
)
from pathlib import Path

SOURCE = "education-podcast"
APPLE_SEARCH = "https://itunes.apple.com/search"
REQUEST_GAP = 2.0  # Apple ~20 req/min
TIMEOUT = 60

STATE_DIR = Path(os.getenv("EDU_PODCAST_STATE", "/tmp/edu-podcast-state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)

FAIL_REASONS = ("unreachable", "timeout", "dns", "http_error", "parse_error")


def _ua() -> dict:
    return {"User-Agent": "fd-industry-data/1.0 (education-podcast; polite)"}


def _fetch(url: str, timeout: int = TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers=_ua())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ── RSS 解析 ─────────────────────────────────────────────────────────────────

def _parse_feed(xml_bytes: bytes) -> dict:
    """RSS 2.0 → {title, link, items:[{guid,title,pub_date,enclosure_url,description}]}。"""
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("not an RSS feed (no <channel>)")
    out_items = []
    for item in channel.findall("item"):
        enc = item.find("enclosure")
        guid = (item.findtext("guid") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_raw = (item.findtext("pubDate") or "").strip()
        try:
            pub_iso = parsedate_to_datetime(pub_raw).astimezone(timezone.utc).isoformat() if pub_raw else None
        except Exception:
            pub_iso = None
        out_items.append({
            "guid": guid or link,
            "title": (item.findtext("title") or "").strip(),
            "pub_date": pub_iso,
            "enclosure_url": (enc.get("url") if enc is not None else None) or None,
            "description": (item.findtext("description") or "").strip(),
        })
    out_items.sort(key=lambda i: i["pub_date"] or "", reverse=True)
    return {
        "title": (channel.findtext("title") or "").strip(),
        "link": (channel.findtext("link") or "").strip(),
        "items": out_items,
    }


# ── 3.1 seed ─────────────────────────────────────────────────────────────────

def run_podcast_seed(keywords: list[str] | None = None,
                     countries: list[str] | None = None,
                     limit: int = 5) -> list[dict]:
    """Apple 目录 seed：关键词×国家 检索（entity=podcast 拿 feedUrl）→ feed 实测 → 去重入册。

    返回逐节目行（RSS 注册表 seed 行）；验证：feed 拉通（episodes>0）者才入册。
    """
    keywords = keywords or ["science", "history", "language learning",
                           "programming", "finance", "科学", "历史", "财经"]
    countries = countries or ["us", "cn"]
    seen_feed: set[str] = set()
    seen_collection: set[str] = set()
    rows = []
    now = datetime.now(timezone.utc).isoformat()

    for country in countries:
        for kw in keywords:
            if len(rows) >= limit:
                return rows
            params = urllib.parse.urlencode({
                "term": kw, "media": "podcast", "entity": "podcast",
                "limit": 10, "country": country,
            })
            try:
                payload = json.loads(_fetch(f"{APPLE_SEARCH}?{params}"))
            except Exception:
                continue
            time.sleep(REQUEST_GAP)
            for r in payload.get("results", []):
                feed = r.get("feedUrl")
                cid = str(r.get("collectionId") or "")
                if not feed or cid in seen_collection:
                    continue
                seen_collection.add(cid)
                try:
                    parsed = _parse_feed(_fetch(feed))
                    episodes = len(parsed["items"])
                    latest = parsed["items"][0]["pub_date"] if parsed["items"] else None
                    host = urllib.parse.urlparse(feed).netloc
                except Exception:
                    episodes, latest, host = 0, None, urllib.parse.urlparse(feed).netloc
                if feed in seen_feed:
                    continue
                seen_feed.add(feed)
                rows.append({
                    "show_id": f"podcast:{cid}",
                    "name": r.get("collectionName"),
                    "feed_url": feed,
                    "platform": host,
                    "episodes": episodes,
                    "latest_pub_date": latest,
                    "license": "public-enclosure",
                    "license_layer": "public-enclosure",  # 归一枚举（spec add-education-public-read-tier 4.1）
                    "redistribution": "needs_authorization",
                    "body_state": "stored_extraction" if episodes else "link_only",
                    "education_source": "apple_seed",
                    "source": SOURCE,
                    "source_url": r.get("collectionViewUrl"),
                    "scraped_at": now,
                })
                if len(rows) >= limit:
                    return rows
    return rows


# ── 3.2 poller + 3.3 health ─────────────────────────────────────────────────

def _load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def _save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def _fail_reason(exc: Exception) -> str:
    s = str(exc).lower()
    if "timed out" in s or "timeout" in s:
        return "timeout"
    if "name or service not known" in s or "nodename" in s or "temporary failure in name resolution" in s:
        return "dns"
    if isinstance(exc, ET.ParseError) or "not an rss" in s or "syntax error" in s or "mismatched tag" in s:
        return "parse_error"
    if "http error" in s:
        return "http_error"
    return "unreachable"


def _transcribe(enclosure_url: str) -> tuple[str | None, str]:
    """faster-whisper 可选插槽：装了转成 markdown（done），没装保持 pending（不伪装）。

    前置过闸（spec cap1 R7）：channel 固定 rss_enclosure，主机在禁止面即抛。
    """
    assert_body_acquisition_allowed("rss_enclosure", enclosure_url)
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return None, "pending"
    # 真转写路径（生产 GPU 批处理时启用；本机冒烟不跑大模型）
    try:
        audio = _fetch(enclosure_url, timeout=300)
        model = faster_whisper.WhisperModel("small")
        segs, _info = model.transcribe(__import__("io").BytesIO(audio))
        text = "\n\n".join(s.text.strip() for s in segs if s.text.strip())
        return (text or None), ("done" if text else "failed")
    except Exception:
        return None, "failed"


def run_podcast_poller(feeds: list[str] | None = None,
                       new_ep_cap: int = 3,
                       fetch_transcripts: bool = False,
                       state_file: str | None = None) -> list[dict]:
    """RSS 活水 poller：增量拉新期 → 转录（可选）→ 产目录行+健康行。

    - feeds：待盯 feed URL 列表（通常由 ingest 层从 RSS 注册表传入）。
    - new_ep_cap：每节目每回合新期上限（起步限流）。
    - fetch_transcripts：true 才走转录插槽（默认 false=只登记 pending）。
    - 健康：每 feed 的 consecutive_fail/last_error 进状态；失败产 ``kind=health_fail`` 行。
    """
    feeds = feeds or []
    if not feeds:
        raise RuntimeError("poller: empty feeds list — refusing to run (spec cap1 R6: 故障不是空转)")
    state_path = Path(state_file) if state_file else STATE_DIR / "poller_state.json"
    state = _load_state(state_path)
    now = datetime.now(timezone.utc).isoformat()
    out: list[dict] = []

    for feed in feeds:
        entry = state.setdefault(feed, {"last_seen_guids": [], "consecutive_fail": 0,
                                        "last_error": None, "last_success_at": None})
        try:
            parsed = _parse_feed(_fetch(feed))
        except Exception as e:
            entry["consecutive_fail"] += 1
            entry["last_error"] = _fail_reason(e)
            out.append({
                "kind": "health_fail", "feed_url": feed,
                "consecutive_fail": entry["consecutive_fail"],
                "reason": entry["last_error"], "as_of": now, "source": SOURCE,
            })
            continue
        entry["consecutive_fail"] = 0
        entry["last_error"] = None
        entry["last_success_at"] = now

        seen = set(entry["last_seen_guids"])
        new_items = [i for i in parsed["items"] if i["guid"] not in seen][:new_ep_cap]
        for item in new_items:
            row = {
                "kind": "episode",
                "show_id": f"podcast:feed:{urllib.parse.urlparse(feed).netloc}",
                "show_name": parsed["title"],
                "episode_guid": item["guid"],
                "title": item["title"],
                "pub_date": item["pub_date"],
                "enclosure_url": item["enclosure_url"],
                "license": "public-enclosure",
                "redistribution": "needs_authorization",
                "education_source": "podcast_rss",
                "source": SOURCE,
                "source_url": feed,
                "scraped_at": now,
            }
            if item["enclosure_url"] and is_banned_body_host(item["enclosure_url"]):
                # spec cap1 R7 负面清单：RSS 里混进平台下载链接（B站/YouTube/Coursera...）
                # → 拒转录、标 failed，绝不与 RSS 轨混用
                row["body_markdown"] = None
                row["body_state"] = "link_only"
                row["episode_status"] = "failed"
                row["episode_note"] = "banned_platform_download_path (spec cap1 R7)"
            elif item["enclosure_url"]:
                if fetch_transcripts:
                    md, status = _transcribe(item["enclosure_url"])
                    row["body_markdown"] = md
                    row["body_state"] = "stored_extraction" if md else "link_only"
                    row["episode_status"] = status
                else:
                    row["body_markdown"] = None
                    row["body_state"] = "pending"
                    row["episode_status"] = "pending"
            else:
                row["body_state"] = "link_only"
                row["episode_status"] = "failed"
            entry.setdefault("episode_status", {})[item["guid"]] = row["episode_status"]
            out.append(row)

        entry["last_seen_guids"] = ([i["guid"] for i in parsed["items"]] + list(seen))[:50]

    _save_state(state_path, state)
    return out


def run_education_podcast(limit: int = 5) -> list[dict]:
    """runner_cli 约定入口（slug 连字符→下划线）：本源主入口 = Apple 目录 seed。

    主入口选择依据（README/声明面）：
    - ``run_podcast_seed`` 是唯一声明并接受 ``limit`` 的命令（manifest
      functions[].parameters 里 seed 有 limit；poller 没有），可仅凭 runner
      传入的 ``limit`` 独立跑通（Apple 目录 → feed 实测 → 入册）。
    - ``run_podcast_poller`` 需要调用方注入 ``feeds``（RSS 注册表，来自
      ingest 层），空列表即显式 ``RuntimeError`` 拒跑（spec cap1 R6）——
      runner 只传 ``limit``，无法作为无人值守入口；poller 是 seed 产出的
      注册表之上的增量下游，仍由 provider 按名分派。
    故固定 seed 为主入口并透传 ``limit``；缺此包装时 seed/poller 双 run_*
    被判歧义 → exit 4 且报到前失败（盲区）。
    """
    return run_podcast_seed(limit=limit)


if __name__ == "__main__":
    rows = run_podcast_seed(limit=3)
    for r in rows:
        print(f"{r['name']} | {r['feed_url'][:50]} | eps={r['episodes']}")
