"""spiders/steam-ccu —— Steam 在线人数（CCU）+ 评论聚合取数。

数据表面（侦察簿 ★接；本机代理复测 2026-10-07）：

- CCU：``GET https://api.steampowered.com/ISteamUserStats/
  GetNumberOfCurrentPlayers/v1/?appid=<id>`` →
  ``{"response": {"player_count": <int>, "result": 1}}``。
  `result != 1` 视为该 app 取数失败（ Steam 对无统计的 appid 回 HTTP 404）。
- 评论聚合：``GET https://store.steampowered.com/appreviews/<id>
  ?json=1&num_per_page=0&language=all&purchase_type=all`` →
  ``query_summary``（review_score / review_score_desc / total_positive /
  total_negative / total_reviews）。``num_per_page=0`` 只取聚合摘要，
  不翻页、不拉评论正文。
- **免 key**：两个端点均无鉴权（Web API 免鉴权面）。
- 种子为固定热门 app 清单（代码常量，含名称映射——名称是种子元数据，
  非抓取字段；CCU/评论端点均不回名称）。

坑位与容错（实测证据，README 逐条展开）：

- **api.steampowered.com 与 store.steampowered.com 均被墙**：必须经代理出口
  （urllib 缺省尊重 ``HTTPS_PROXY``/``HTTP_PROXY`` 环境变量；本单元不硬编码
  代理地址）。
- **CCU/评论均为实时数**：值随时间波动，**golden 只锚结构断言 + min_rows，
  绝不锚任何数值**（含 ccu、total_*、positive_ratio）。
- 坏/下架 appid：CCU 端 HTTP 404；评论端 HTTP 200 + ``total_reviews=0``、
  ``review_score_desc="No user reviews"``（不报错）——单 app 两路任一失败
  即整 app 跳过并留痕，**全部 app 失败抛 ``RuntimeError``**。
- 评论端 ``success != 1`` 视为失败（实测正常恒为 1）。
- 单 app 两请求（CCU+评论），app 间固定 0.2s 礼貌间隔；无重试、无频次放大。

无工单：机器重启恢复直建（批次二 Wave C），入口函数与行数在 PR body 手工留证。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE = "steam-ccu"
CCU_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
REVIEWS_URL = "https://store.steampowered.com/appreviews/{appid}"

# 站点对 UA 不敏感（实测缺省 Python UA 可通）；带常规 UA 统一姿态，
# 不做指纹伪装与频次对抗。
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
}

# 种子 app 清单：固定热门游戏（appid, 名称）。名称为种子元数据（两个端点
# 均不回名称），非抓取字段；appid 变更/下架由失败留痕暴露。
SEEDS = (
    (730, "Counter-Strike 2"),
    (570, "Dota 2"),
    (578080, "PUBG: BATTLEGROUNDS"),
    (271590, "Grand Theft Auto V"),
    (1172470, "Apex Legends"),
    (252490, "Rust"),
    (359550, "Tom Clancy's Rainbow Six Siege"),
    (1085660, "Destiny 2"),
    (1938090, "Call of Duty"),
    (2358720, "Black Myth: Wukong"),
    (1203220, "NARAKA: BLADEPOINT"),
    (2246340, "Monster Hunter Wilds"),
)

TIMEOUT = 30  # 秒；单请求超时即该 app 失败（不重试、不加频次）
INTER_REQUEST_SLEEP = 0.2  # 秒；app 间礼貌间隔


def _get_json(url: str) -> dict:
    """GET 一个 JSON 端点（仅 https 固定主机；无鉴权、无重试放大）。

    代理出口由环境变量 ``HTTPS_PROXY``/``HTTP_PROXY`` 提供（urllib 缺省
    行为），本函数不硬编码代理地址。
    """
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"response is not JSON ({type(exc).__name__})") from None


def _fetch_ccu(appid: int) -> int:
    """取单个 app 的 CCU；无统计/坏 appid/形态异常 → 抛 ``ValueError``。"""
    query = urllib.parse.urlencode({"appid": appid})
    payload = _get_json(f"{CCU_URL}?{query}")
    response = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(response, dict) or response.get("result") != 1:
        raise ValueError(f"CCU response malformed or result!=1: {str(payload)[:120]}")
    ccu = response.get("player_count")
    if not isinstance(ccu, int) or isinstance(ccu, bool) or ccu < 0:
        raise ValueError(f"CCU player_count invalid: {ccu!r}")
    return ccu


def _fetch_reviews(appid: int) -> dict:
    """取单个 app 的评论聚合摘要（num_per_page=0，不拉正文）；异常 → ``ValueError``。"""
    query = urllib.parse.urlencode({
        "json": 1,
        "num_per_page": 0,
        "language": "all",
        "purchase_type": "all",
    })
    payload = _get_json(f"{REVIEWS_URL.format(appid=appid)}?{query}")
    if not isinstance(payload, dict) or payload.get("success") != 1:
        raise ValueError(f"reviews success!=1: {str(payload)[:120]}")
    summary = payload.get("query_summary")
    if not isinstance(summary, dict):
        raise ValueError("reviews response has no query_summary")
    return summary


def _num(summary: dict, key: str) -> int | None:
    value = summary.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def run_steam_ccu(limit: int = 100) -> list[dict]:
    """取种子 app 的 CCU + 评论聚合行；全部 app 失败 → 抛 ``RuntimeError``（失败即红）。

    - 每个 app 一行：CCU 在线人数 + 评论聚合（好评率等派生值 round 4 位）；
    - 单 app 两路任一失败即整行跳过并留痕（errors 收集，不中断其余 app）；
    - 行按种子顺序排列，返回尾部 ``limit`` 行；
    - golden 只锚结构断言（常量字段）+ min_rows，**绝不锚实时数值**。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    errors: list[str] = []
    for i, (appid, name) in enumerate(SEEDS):
        if i:
            time.sleep(INTER_REQUEST_SLEEP)
        try:
            ccu = _fetch_ccu(appid)
            summary = _fetch_reviews(appid)
        except Exception as exc:  # noqa: BLE001 - 单 app 失败只跳过并留痕
            errors.append(f"{appid}: {type(exc).__name__}: {exc}")
            continue
        total_positive = _num(summary, "total_positive")
        total_negative = _num(summary, "total_negative")
        total_reviews = _num(summary, "total_reviews")
        if total_reviews:
            positive_ratio = (
                round(total_positive / total_reviews * 100, 4)
                if total_positive is not None else None
            )
        else:
            positive_ratio = None
        rows.append({
            "appid": appid,
            "name": name,
            "ccu": ccu,
            "review_score": _num(summary, "review_score"),
            "review_score_desc": str(summary.get("review_score_desc", "")).strip() or None,
            "total_positive": total_positive,
            "total_negative": total_negative,
            "total_reviews": total_reviews,
            "positive_ratio": positive_ratio,
            "url": f"{REVIEWS_URL.format(appid=appid)}",
            "source": SOURCE,
            "scraped_at": now,
        })
    if not rows:
        raise RuntimeError("steam-ccu: 全部种子 app 取数失败 — " + "; ".join(errors)[:300])
    return rows[-limit:]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/steam-ccu/spider.py
    print(json.dumps(run_steam_ccu(limit=3), ensure_ascii=False, indent=2))
