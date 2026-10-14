"""spiders/entsoe-e —— 欧洲输电运营商联盟 ENTSO-E 透明平台电力序列（REST API）。

数据表面（Transparency Platform RESTful API，**securityToken 鉴权**，实测 2026-10-14）：

- ``GET https://web-api.tp.entsoe.eu/api``
  查询参数：``securityToken``（env ``ENTSOE_SECURITY_TOKEN``）、``documentType``、
  ``processType``、``in_Domain``/``out_Domain``（发电/负荷）或
  ``outBiddingZone_Domain``（负荷侧）、``periodStart``/``periodEnd``（**UTC
  ``YYYYMMDDHHMM`` 硬格式**）。

两个受支持文档类型（同构解析，``GL_MarketDocument``）：

- ``A75`` 分燃料类型实际发电（Actual generation per production type）：
  ``MktPSRType/psrType`` 区分燃料（B01 生物质/B02 褐煤/B03 天然气/B04 硬煤/
  B05 燃油/B06 水电/B10 抽蓄/B11 风电/B12 太阳能/B14 核能/…），
  ``businessType=A01``（实测 CZ 2020-01-01..07 返 16 个 TimeSeries）。
- ``A65`` 总负荷（Actual total load）：``outBiddingZone_Domain`` +
  ``businessType=A04``（实测 CZ 同日单 TimeSeries）。

行口径：每个 ``TimeSeries`` 的 ``Period`` 下每 ``Point`` 一行——
``period``（该点所属 UTC 小时桶 ``YYYY-MM-DDTHH:MMZ``，按 ``resolution``
从 ``timeInterval.start`` 步进）+ ``position`` + ``quantity``（MW，
``quantity_Measure_Unit.name`` 实测 ``MAW``）+ psr_type/business_type/zone。

口坑（工单 §3.2⑪ 逐条落实）：

- **唯一人工审批源**：token 未到位必须显式报错——缺 env 直接 ``RuntimeError``，
  token 无效返 HTTP 401 + ``Acknowledgement_MarketDocument``（code 999
  "Authentication failed."），同样抛 ``RuntimeError``（失败即红，绝不静默空列表）。
- **securityToken 必须走查询参数**：实测 ``securityToken`` 请求头形式返 401
  code 999（2026-10-14 实锤），仅 query 参数可用。
- 时间参数必须 UTC ``YYYYMMDDHHMM``；``periodStart>=periodEnd`` 服务端 400。
- 只返 XML：``xml.etree`` 按本地名解析（命名空间 ``urn:iec62325.351:...``）。
- 限速社区口径 ~400 次/分（超限临时封 10 分钟）——每次调用只发 1 个请求，
  ≤1 req/s 自限；429/5xx 退避一次重试（2 秒），仍失败即红。
- **滚动窗**：默认取最近 7 个完整 UTC 日（``days_back=7``）；更早历史可回补
  （平台留存 2015 起），golden 锚固定历史期（CZ 2020-01-01..07）恒定。
- 免 key 之外的其它鉴权、不做指纹伪装与频次对抗，遇风控升级按协议转人工。
"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

SOURCE = "entsoe-e"
BASE = "https://web-api.tp.entsoe.eu/api"
TIMEOUT = 60              # 秒/请求；A75 一周 XML 约 310KB
RETRY_BACKOFF_S = 2.0     # 429/5xx 退避一次；仍失败即红
_UA = "fd-industry-data/2.0 (entsoe-e; industry data pipeline)"

# 支持的文档类型 → （查询参数名, businessType 种子, 说明）
DOC_TYPES = {
    "A75": {"zone_param": "in_Domain", "business_type": "A01",
            "label": "actual generation per production type"},
    "A65": {"zone_param": "outBiddingZone_Domain", "business_type": "A04",
            "label": "actual total load"},
}
DEFAULT_ZONE = "10YCZ-CEPS-----N"   # 捷克 bidding zone（golden 锚定同区）
DEFAULT_DOC = "A75"
DEFAULT_DAYS_BACK = 7


def _local(tag: str) -> str:
    """元素本地名（对 IEC 命名空间前缀不敏感）。"""
    return tag.rsplit("}", 1)[-1]


def _token() -> str:
    """读取 securityToken；缺失即显式报错（审批源纪律：不裸跑）。"""
    token = (os.environ.get("ENTSOE_SECURITY_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(
            "entsoe-e: 缺少 ENTSOE_SECURITY_TOKEN 环境变量——ENTSO-E 为人工审批源，"
            "token 未注入时禁止运行（平台对无 token 请求返 401+code999）")
    return token


def _get_xml(url: str, safe_url: str) -> ET.Element:
    """GET 并解析 XML；429/5xx 退避重试一次。

    错误信息一律用 ``safe_url``（token 已脱敏）——凭据绝不进异常文本/日志。
    """
    last_exc: Exception | None = None
    for attempt in (1, 2):
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/xml"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
                return ET.fromstring(resp.read())
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:4096]
            except Exception:  # noqa: BLE001
                pass
            reason = ""
            try:
                root = ET.fromstring(body)
                for el in root.iter():
                    if _local(el.tag) == "text":
                        reason = (el.text or "").strip()
                        break
            except ET.ParseError:
                pass
            detail = f"HTTP {exc.code}" + (f": {reason}" if reason else f": {body[:200]}")
            if exc.code in (429, 500, 502, 503, 504) and attempt == 1:
                last_exc = exc
                time.sleep(RETRY_BACKOFF_S)
                continue
            raise RuntimeError(f"entsoe-e: 请求失败 {detail} ({safe_url})") from exc
        except Exception as exc:  # noqa: BLE001 —— 网络层失败即红
            raise RuntimeError(
                f"entsoe-e: 请求失败 {type(exc).__name__}: {exc} ({safe_url})") from exc
    raise RuntimeError(f"entsoe-e: 重试后仍失败: {last_exc}")


def _fmt_utc(dt: datetime) -> str:
    """UTC ``YYYYMMDDHHMM`` 硬格式（API 只认这个）。"""
    return dt.strftime("%Y%m%d%H%M")


def _points(root: ET.Element, ts: ET.Element) -> list[dict]:
    """展开一个 TimeSeries 的所有 Point；period 按 resolution 从 start 步进。"""
    psr_type = None
    for el in ts.iter():
        if _local(el.tag) == "psrType":
            psr_type = (el.text or "").strip() or None
            break
    business_type = None
    for el in ts:
        if _local(el.tag) == "businessType":
            business_type = (el.text or "").strip() or None
            break
    zone = None
    for el in ts:
        if _local(el.tag).endswith("_Domain.mRID"):
            zone = (el.text or "").strip() or None
            break

    rows: list[dict] = []
    for period in ts:
        if _local(period.tag) != "Period":
            continue
        start_raw = resolution = None
        for el in period.iter():
            name = _local(el.tag)
            if name == "start" and start_raw is None:
                start_raw = (el.text or "").strip()
            elif name == "resolution" and resolution is None:
                resolution = (el.text or "").strip()
        if start_raw is None or resolution is None:
            continue
        try:
            start_dt = datetime.strptime(start_raw, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        step_min = {"PT15M": 15, "PT30M": 30, "PT60M": 60, "P1D": 1440}.get(resolution)
        if step_min is None:
            continue
        for point in period:
            if _local(point.tag) != "Point":
                continue
            position = quantity = None
            for el in point:
                name = _local(el.tag)
                if name == "position":
                    position = (el.text or "").strip()
                elif name == "quantity":
                    quantity = (el.text or "").strip()
            if position is None or quantity is None:
                continue
            try:
                pos = int(position)
                qty = float(quantity)
            except ValueError:
                continue
            point_dt = start_dt + timedelta(minutes=step_min * (pos - 1))
            rows.append({
                "period": point_dt.strftime("%Y-%m-%dT%H:%MZ"),
                "position": pos,
                "quantity_mw": qty,
                "psr_type": psr_type,
                "business_type": business_type,
                "zone": zone,
                "resolution": resolution,
            })
    return rows


def run_entsoe_e(
    limit: int = 100,
    document_type: str = DEFAULT_DOC,
    zone: str = DEFAULT_ZONE,
    days_back: int = DEFAULT_DAYS_BACK,
    period_start: str | None = None,
    period_end: str | None = None,
) -> list[dict]:
    """取 ENTSO-E 透明平台一个文档类型 × 一个 bidding zone 的序列点。

    Args:
        limit: 返回行数上限；按期次升序保留**最近** ``limit`` 行（生产取最新）。
        document_type: ``A75``（分燃料发电）或 ``A65``（总负荷）。
        zone: ENTSO-E EIC 区域码（默认捷克 ``10YCZ-CEPS-----N``）。
        days_back: 未显式给 period_start/end 时，回看的完整 UTC 天数（默认 7）。
        period_start/period_end: 显式 UTC ``YYYYMMDDHHMM`` 窗口（golden 锚定用途）。

    Returns:
        每行含 period/position/quantity_mw/psr_type/business_type/zone/resolution
        + url/source/scraped_at。

    Raises:
        RuntimeError: 缺 token、请求失败（401/400/网络）、XML 形态异常或零可解析行。
    """
    token = _token()                                  # 缺 token 立即红（不裸跑）
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 20000))
    doc = str(document_type or DEFAULT_DOC).upper()
    spec = DOC_TYPES.get(doc)
    if spec is None:
        raise RuntimeError(
            f"entsoe-e: 不支持的 document_type={document_type!r}"
            f"（支持 {sorted(DOC_TYPES)}）")

    if period_start and period_end:
        p_start, p_end = str(period_start), str(period_end)
    else:
        try:
            days = max(1, min(int(days_back), 365))
        except (TypeError, ValueError):
            days = DEFAULT_DAYS_BACK
        end_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start_dt = end_dt - timedelta(days=days)
        p_start, p_end = _fmt_utc(start_dt), _fmt_utc(end_dt)

    params = {
        "securityToken": token,
        "documentType": doc,
        "processType": "A16",
        spec["zone_param"]: str(zone),
        "periodStart": p_start,
        "periodEnd": p_end,
    }
    url = f"{BASE}?{urllib.parse.urlencode(params)}"
    safe_url = url.replace(urllib.parse.quote(token, safe=""), "REDACTED").replace(token, "REDACTED")
    root = _get_xml(url, safe_url)

    if _local(root.tag) == "Acknowledgement_MarketDocument":
        reason = ""
        for el in root.iter():
            if _local(el.tag) == "text":
                reason = (el.text or "").strip()
                break
        raise RuntimeError(f"entsoe-e: API 返回错误文档: {reason or 'unknown'} ({safe_url})")

    series = [el for el in root.iter() if _local(el.tag) == "TimeSeries"]
    if not series:
        raise RuntimeError(
            f"entsoe-e: 响应无 TimeSeries（doc={doc} zone={zone} {p_start}..{p_end}）——"
            "区域码/期次无效或该窗无数据")

    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    for ts in series:
        for row in _points(root, ts):
            row["url"] = safe_url            # token 脱敏：凭据永不入行/入库
            row["source"] = SOURCE
            row["scraped_at"] = scraped_at
            rows.append(row)

    if not rows:
        raise RuntimeError(
            f"entsoe-e: 取到 0 行可解析点（doc={doc} zone={zone} {p_start}..{p_end}）")

    rows.sort(key=lambda r: (r["period"], r["position"], r["psr_type"] or ""))
    return rows[-limit:]
