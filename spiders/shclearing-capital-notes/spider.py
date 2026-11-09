"""上海清算所 商业银行资本工具（二级资本债券 / 无固定期限资本债券）发行与流通要素。

端点（本轮实读打通，2026-11-08）：

``POST https://www.shclearing.com.cn/shchapp/web/disclosureForTrsServer/search``
（TRS 公告检索）。必需 header：浏览器 ``User-Agent`` + 同栏目 ``Referer``
（``/xxpl/jyltysgg/`` 流通 / ``/xxpl/fxpl/`` 发行）；
``X-Requested-With: XMLHttpRequest`` 与 ``Content-Type`` 按表单发送。**无需 Cookie**。

参数（实读）：

- ``channelId``：``166`` = 交易流通要素公告（流通要素），``144`` = 发行披露（发行要素）
- ``keyword``：标题全文检索。上清所标题里永续债写作「无固定期限资本债券」
  （搜「永续债」0 条）
- ``start`` / ``limit``：**ExtJS 口径的偏移 + 条数**（不是 pageNo/pageSize，
  后两者实测被忽略）。``start=10&limit=10`` 与 ``start=20&limit=10`` 返回的
  首条不同；``limit=200`` 一次可取 200 条。

响应 ``{success, root:[{title,url,publishDate,channelId}], totalProperty}``；
``totalProperty`` 是该关键词的命中总数，``root`` 长度即本页条数。

详情页是「基本信息 / 其他信息」两列表格（label-value），走共享层
``fd_industry_data.tabular`` 的 HTML 通道解析（``read_grid(payload, table=i)``），
不在本单元手写表格解析。
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from fd_industry_data import tabular

SOURCE = "shclearing-capital-notes"
BASE = "https://www.shclearing.com.cn"
TRS_URL = BASE + "/shchapp/web/disclosureForTrsServer/search"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

#: channelId → (channel 值, 中文栏目名, 该栏目 Referer)
CHANNELS: dict[str, tuple[str, str, str]] = {
    "166": ("circulation", "交易流通要素公告", BASE + "/xxpl/jyltysgg/"),
    "144": ("issuance", "发行披露", BASE + "/xxpl/fxpl/"),
}
#: 抓取顺序：先流通（字段最全：代码/总额/期限/票面/评级），再发行。
CHANNEL_ORDER = ("166", "144")
DEFAULT_KEYWORDS = ("二级资本债券", "无固定期限资本债券")

PAGE_SIZE = 50
MAX_PAGES = 12  # 每个（栏目, 关键词）最多翻多少页，防止详情页无匹配时遍历整库
SLEEP_S = 1.0
MAX_DETAIL_TABLES = 20
_MISSING = {"", "-", "--", "—", "---", "…", "/", "不适用"}
_TERM_YEARS = re.compile(r"(\d+(?:\.\d+)?)\s*年")
_LEGAL_SUFFIX = re.compile(r"[（(].*?[)）]")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _request(url: str, *, data: dict[str, str] | None = None, referer: str) -> bytes:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    headers = {"User-Agent": UA, "Referer": referer}
    if body is not None:
        headers["X-Requested-With"] = "XMLHttpRequest"
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    req = urllib.request.Request(url, data=body, headers=headers)
    return urllib.request.urlopen(req, timeout=60).read()


def _search(channel: str, keyword: str, start: int, limit: int) -> tuple[list[dict], int]:
    """TRS 检索一页，返回 (root 列表, totalProperty)。"""
    payload = _request(
        TRS_URL,
        data={
            "channelId": channel,
            "keyword": keyword,
            "start": str(int(start)),
            "limit": str(int(limit)),
        },
        referer=CHANNELS[channel][2],
    )
    import json

    text = payload.decode("utf-8", "replace").strip()
    if not text.startswith("{"):
        raise ValueError(f"TRS search 非 JSON（{text[:80]!r}）")
    doc = json.loads(text)
    if not doc.get("success"):
        raise ValueError(f"TRS search success=false（{text[:120]!r}）")
    items = doc.get("root") or []
    try:
        total = int(doc.get("totalProperty") or 0)
    except (TypeError, ValueError):
        total = 0
    return items, total


def _pairs(payload: bytes) -> dict[str, str]:
    """详情页所有表格的 label→value 对（多列行按两两切分，先到先得）。"""
    pairs: dict[str, str] = {}
    for idx in range(MAX_DETAIL_TABLES):
        try:
            grid = tabular.read_grid(payload, table=idx)
        except tabular.TabularError:
            break
        for row in grid:
            cells = [str(c or "").strip() for c in row]
            for j in range(0, len(cells) - 1, 2):
                label, value = cells[j], cells[j + 1]
                if label and value and label not in pairs:
                    pairs[label] = value
    return pairs


def _label(text: str) -> str:
    """去掉单位括注后的字段名（``发行（创设）总额（亿元）`` → ``发行总额``）。"""
    return _LEGAL_SUFFIX.sub("", text or "").strip()


def _pick(norm: dict[str, str], *needles: str) -> str:
    for needle in needles:
        if needle in norm:
            return norm[needle]
    for needle in needles:
        for key, value in norm.items():
            if needle in key:
                return value
    return ""


def _num(text: str) -> float | None:
    value = (text or "").replace(",", "").replace("%", "").replace("\u00a0", "").strip()
    if value in _MISSING:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _term_years(term: str) -> float | None:
    matched = _TERM_YEARS.search(term or "")
    return float(matched.group(1)) if matched else None


def _detail_row(
    url: str, channel: str, publish_date: str, stamp: str, title: str = ""
) -> dict | None:
    payload = _request(url, referer=CHANNELS[channel][2])
    pairs = _pairs(payload)
    norm = {_label(k): v for k, v in pairs.items() if v and v not in _MISSING}

    bond_code = _pick(norm, "产品代码", "债券代码", "代码")
    short_name = _pick(norm, "产品简称", "债券简称", "简称")
    full_name = _pick(norm, "产品全称", "债券全称", "全称")
    if not (bond_code or short_name or full_name):
        if channel == "144" and title.strip():
            # 实测：ch144 发行披露的详情页是自由文本公告（申购提示/发行公告），
            # 8/8 抽样无 label-value 要素表。保留公告级行（标题+日期+URL），
            # 数值字段留空（null）——不猜值、不伪造要素。
            full_name = title.strip()
        else:
            return None  # 检索命中但不是要素公告（如财务报告），不出行

    term = _pick(norm, "期限")
    code = CHANNELS[channel][0]
    return {
        "channel": code,
        "channel_label": CHANNELS[channel][1],
        "bond_name": full_name or None,
        "bond_short_name": short_name or None,
        "bond_code": bond_code or None,
        "issue_amount": _num(_pick(norm, "发行总额", "计划发行总额", "实际发行总额")),
        "amount_unit": "亿元",
        "term": term or None,
        "term_years": _term_years(term),
        "coupon_rate": _num(_pick(norm, "票面年利率", "票面利率")),
        "coupon_unit": "%",
        "product_rating": _pick(norm, "产品评级", "债项评级", "债券评级") or None,
        "issuer_rating": _pick(norm, "主体评级") or None,
        "rating_agency": _pick(norm, "产品评级机构", "评级机构") or None,
        "issue_date": _pick(norm, "发行日") or None,
        "circulation_date": _pick(norm, "流通日") or None,
        "circulation_end_date": _pick(norm, "流通结束日") or None,
        "maturity_date": _pick(norm, "到期日") or None,
        "interest_method": _pick(norm, "计息方式") or None,
        "publish_date": publish_date or None,
        "detail_url": url,
        "source": SOURCE,
        "scraped_at": stamp,
    }


def run_shclearing_capital_notes(limit: int = 200, keyword: str = "") -> list[dict]:
    """上清所商业银行资本工具发行/流通要素（每只债·每类公告一行）。

    Args:
        limit: 返回行数上限（真实生效：达到上限立即停止后续详情页请求）。
        keyword: 标题检索词；给定时**只**抓该关键词（不再叠加两个默认词）。
            空 = 依次抓「二级资本债券」「无固定期限资本债券」。

    Raises:
        RuntimeError: 所有栏目/关键词均无可用要素公告。
    """
    if limit <= 0:
        return []
    keywords = [keyword.strip()] if keyword and keyword.strip() else list(DEFAULT_KEYWORDS)
    stamp = _now()
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for pos, channel in enumerate(CHANNEL_ORDER):
        # 两个栏目都必须覆盖：流通栏目命中量最大（ch166「二级资本债券」390 条），
        # 若与发行共用「先到先吃满」的预算，ch144 永远不会被触达。
        # 故首个栏目取一半额度，其余栏目用剩余额度（总行数仍 ≤ limit）。
        budget = max(1, int(limit) // 2) if pos == 0 else int(limit) - len(rows)
        if budget <= 0:
            continue
        produced = 0
        for word in keywords:
            start = 0
            pages = 0
            while produced < budget and pages < MAX_PAGES:
                pages += 1
                try:
                    items, total = _search(channel, word, start, PAGE_SIZE)
                except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                    print(
                        f"[{SOURCE}] ch{channel} 关键词{word!r} start={start} 检索失败：{exc}",
                        flush=True,
                    )
                    break
                if not items:
                    break
                for item in items:
                    if produced >= budget:
                        break
                    url = str(item.get("url") or "").strip()
                    if not url or (channel, url) in seen:
                        continue
                    seen.add((channel, url))
                    try:
                        row = _detail_row(
                            url,
                            channel,
                            str(item.get("publishDate") or ""),
                            stamp,
                            str(item.get("title") or ""),
                        )
                    except (urllib.error.URLError, TimeoutError, ValueError, tabular.TabularError) as exc:
                        print(f"[{SOURCE}] 详情失败 {url}：{exc}", flush=True)
                        row = None
                    time.sleep(SLEEP_S)
                    if row is not None:
                        rows.append(row)
                        produced += 1
                        print(
                            f"[{SOURCE}] ch{channel} {row['bond_code'] or '-'} "
                            f"{row['bond_short_name'] or row['bond_name'] or '-'}",
                            flush=True,
                        )
                start += len(items)
                if start >= total or len(items) < PAGE_SIZE:
                    break

    if not rows:
        raise RuntimeError("shclearing-capital-notes: TRS 检索无可用资本工具要素公告")
    return rows[: int(limit)]
