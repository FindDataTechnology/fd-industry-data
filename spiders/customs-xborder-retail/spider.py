"""customs-xborder-retail —— 中国海关总署跨境电商零售进出口统计（月度/年度）。

数据源（2026-11 实测）：
- 官方统计查询平台：http://stats.customs.gov.cn/（海关统计数据查询平台）
- 海关总署门户新闻发布：https://www.customs.gov.cn/（统计分析/新闻发布栏目）

实测可达性（2026-11-03）：
- stats.customs.gov.cn：全站部署 JSL（极速云）防护，curl 直连返回 504 Gateway Time-out / 412 Precondition Failed
- www.customs.gov.cn：同 JSL 防护，统计分析栏目页面同样返回 504/412
- 两站均无公开无鉴权 API 端点可直接查询 9610/1210 零售口径月度数据

按分诊纪律：遇反爬/验证码/防护层 → 契约层，**不许逆向**（不绕过 JSL、不破解 JS 挑战、不抓包逆向内部接口）。
降级策略：单元照常交付 manifest + spider，command 实现为如实报错（返回原因分类：upstream-protection），
报告中标注「契约层待人工」。

预期数据口径（上游发布口径，供人工点亮后对齐）：
- 周期：月度（YYYY-MM）与年度（YYYY）
- 方向：出口、进口、合计
- 业务模式：9610（跨境电商零售出口）、1210（跨境电商零售进口）
- 字段：period, direction, value, unit, indicator_name, source_title, source_url, publish_date
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

SOURCE = "customs-xborder-retail"
STATS_URL = "http://stats.customs.gov.cn/"
MAIN_URL = "https://www.customs.gov.cn/"
TIMEOUT = 20  # 秒；国内政府站直连实测 <10s，防护层拦截即快速失败

logger = logging.getLogger(SOURCE)

# JSL 防护特征码
_JSL_COOKIES = ("__jsluid_s", "__jsluid_h", "__jsl_clearance_s")
_JSL_HEADERS = ("x-via-jsl", "x-jsl")


def _classify_error(exc: Exception, url: str, status: int | None = None, body_snippet: str = "") -> tuple[str, str]:
    """将异常归因为标准分类：network / upstream / quota / parse / upstream-protection。

    Returns: (category, detail)
    """
    # 上游防护层（JSL/WAF/验证码/频控）
    if status in (403, 412, 429, 503, 504):
        return "upstream-protection", f"HTTP {status} 疑似防护层拦截 (JSL/WAF)"
    if any(k in body_snippet.lower() for k in ("jsl", "challenge", "captcha", "验证码", "频繁", "拦截", "防护")):
        return "upstream-protection", f"响应体含防护特征: {body_snippet[:120]}"

    # 网络层
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return "network", f"{type(exc).__name__}: {exc}"

    # 配额/限流（通常 429 或特定错误码）
    if status == 429 or "quota" in str(exc).lower() or "limit" in str(exc).lower():
        return "quota", f"{type(exc).__name__}: {exc}"

    # 解析层
    if isinstance(exc, (json.JSONDecodeError, ValueError, KeyError)):
        return "parse", f"{type(exc).__name__}: {exc}"

    # 其余归为上游异常
    return "upstream", f"{type(exc).__name__}: {exc}"


def _fetch(url: str, headers: dict | None = None) -> tuple[int, str, dict]:
    """发起 GET 请求，返回 (status, body, headers_dict)。非 2xx 也返回而不抛异常，由调用方判断。"""
    req_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": MAIN_URL,
    }
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定主机)
            status = resp.status
            body = resp.read().decode("utf-8", "replace")
            resp_headers = dict(resp.headers)
    except urllib.error.HTTPError as exc:
        # 非 2xx 也读取响应体与头，便于分类
        status = exc.code
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        resp_headers = dict(exc.headers) if exc.headers else {}
    return status, body, resp_headers


def _has_jsl_protection(headers: dict, body: str) -> bool:
    """检测响应是否被 JSL/WAF 拦截。"""
    hdr_str = " ".join(f"{k}:{v}" for k, v in headers.items()).lower()
    if any(k in hdr_str for k in _JSL_HEADERS):
        return True
    if any(c in str(headers.get("Set-Cookie", "")).lower() for c in _JSL_COOKIES):
        return True
    if re.search(r"jsl|challenge|captcha|验证码|频繁|拦截|防护", body, re.I):
        return True
    return False


def _try_fetch_stats_portal() -> list[dict]:
    """尝试访问 stats.customs.gov.cn 统计查询平台首页。"""
    url = STATS_URL
    try:
        status, body, headers = _fetch(url)
        if _has_jsl_protection(headers, body):
            cat, detail = _classify_error(RuntimeError("JSL protection"), url, status, body[:200])
            raise RuntimeError(f"{cat}: {detail}")
        # 若真能拿到页面，这里可扩展解析；当前仅作可达性探测
        logger.info("stats.customs.gov.cn 可达，status=%d", status)
        return []
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        cat, detail = _classify_error(exc, url)
        raise RuntimeError(f"{cat}: {detail}") from exc


def _try_fetch_main_site_news() -> list[dict]:
    """尝试访问 www.customs.gov.cn 新闻发布/统计分析栏目列表页。"""
    # 常见栏目路径（实测均受 JSL 防护）
    candidate_paths = [
        "/customs/302249/index.html",              # 统计分析首页
        "/customs/302249/302266/index.html",       # 月度统计
        "/customs/302249/302266/302267/index.html", # 进出口统计
        "/customs/302249/302266/302267/4486526/index.html", # 跨境电商专题（推测路径）
    ]
    for path in candidate_paths:
        url = MAIN_URL.rstrip("/") + path
        try:
            status, body, headers = _fetch(url)
            if _has_jsl_protection(headers, body):
                cat, detail = _classify_error(RuntimeError("JSL protection"), url, status, body[:200])
                raise RuntimeError(f"{cat}: {detail}")
            logger.info("%s 可达，status=%d", url, status)
            # 若可达，可在此解析列表页链接；当前仅作探测
            return []
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            cat, detail = _classify_error(exc, url)
            raise RuntimeError(f"{cat}: {detail}") from exc
    # 所有候选路径均不可达
    raise RuntimeError("upstream-protection: 所有候选栏目页均被 JSL 防护拦截（504/412）")


def run_customs_xborder_retail(limit: int = 100) -> list[dict]:
    """fd-runner 唯一入口：海关跨境电商零售进出口统计。

    当前实现：
    1. 探测 stats.customs.gov.cn 统计查询平台
    2. 探测 www.customs.gov.cn 新闻发布/统计分析栏目
    3. 均不可达时，返回单条结构化错误行（category=upstream-protection），不静默返回空列表

    契约层待人工：需人工确认上游是否开放无鉴权 API、或提供数据下载链接、或需申请白名单/鉴权。
    点亮前必须完成 golden 样本录入与双闸验证。
    """
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    errors: list[tuple[str, str]] = []  # (category, detail)

    # 1) 尝试统计查询平台
    try:
        _try_fetch_stats_portal()
    except RuntimeError as exc:
        cat, detail = str(exc).split(":", 1) if ":" in str(exc) else ("upstream", str(exc))
        errors.append((cat.strip(), detail.strip()))

    # 2) 尝试主站新闻/统计栏目
    try:
        _try_fetch_main_site_news()
    except RuntimeError as exc:
        cat, detail = str(exc).split(":", 1) if ":" in str(exc) else ("upstream", str(exc))
        errors.append((cat.strip(), detail.strip()))

    # 汇总：若有任何 upstream-protection，视为契约层阻断
    protection_errors = [e for e in errors if e[0] == "upstream-protection"]
    if protection_errors:
        # 返回结构化错误行，便于上游巡检/工单识别
        return [{
            "period": "",
            "direction": "",
            "value": None,
            "unit": "",
            "indicator_name": "海关跨境电商零售进出口统计（契约层待人工）",
            "source_title": "中国海关总署 - 跨境电商零售进出口月度/年度统计 (9610/1210)",
            "source_url": STATS_URL,
            "publish_date": "",
            "_error_category": "upstream-protection",
            "_error_detail": "; ".join(d for c, d in protection_errors),
            "_probed_at": now_iso,
            "_probed_urls": [STATS_URL, MAIN_URL + "/customs/302249/index.html"],
        }]

    # 其他错误类别：记录但不阻断（按分诊纪律，网络/配额/解析错误不应常态化出现）
    if errors:
        return [{
            "period": "",
            "direction": "",
            "value": None,
            "unit": "",
            "indicator_name": "海关跨境电商零售进出口统计（探测异常）",
            "source_title": "中国海关总署 - 跨境电商零售进出口月度/年度统计 (9610/1210)",
            "source_url": STATS_URL,
            "publish_date": "",
            "_error_category": errors[0][0],
            "_error_detail": errors[0][1],
            "_probed_at": now_iso,
            "_probed_urls": [STATS_URL],
        }]

    # 理论上不应走到这里（均可达但无数据解析逻辑），返回空列表触发上游告警
    logger.warning("customs-xborder-retail: 探测均可达但无解析逻辑，返回空列表")
    return []


if __name__ == "__main__":  # 冒烟：python3 spiders/customs-xborder-retail/spider.py
    logging.basicConfig(level=logging.INFO)
    rows = run_customs_xborder_retail(limit=5)
    print(json.dumps(rows, ensure_ascii=False, indent=2))