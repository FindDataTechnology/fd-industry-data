"""spiders/jp-tdnet —— TDNet 適時開示（日本版"及时披露"）日次列表，无键 HTML。

为什么有它（add-restaurant-vertical 3.4）：日股 4 家（`7550.T` 泉盛控股 / `7581.T`
萨莉亚 / `2702.T` 日本麦当劳控股 / `3197.T` 云雀控股）的事件面原本挂 `edinet-api`，
而 EDINET API v2 需要 `Subscription-Key`（须 `data-sources@` 域邮箱申请）。TDNet 的
日次列表页是**公开无键 HTML**，且覆盖 `決算短信` / `業績予想の修正`（即日本版的盈利
预警/预告），正是值守岗要读的那类事件。

数据来源与形态（2026-11-09 实测）：

- 页面：``https://www.release.tdnet.info/inbs/I_list_001_YYYYMMDD.html``
  （`001` = 東証。整页约 18–22 KB，UTF-8，实测 HTTP 200）
- 表头：``時刻 / コード / 会社名 / 表題 / XBRL / 上場取引所 / 更新履歴``
- 行：``<td class="oddnew-L kjTime">10:30</td>``、
  ``<td class="oddnew-M kjCode">42720</td>``、
  ``<td class="oddnew-M kjName">日化薬 </td>``、
  ``<td class="oddnew-M kjTitle"><a href="140120261109568489.pdf">…</a></td>``、
  ``<td class="oddnew-M kjPlace">東 </td>``
- **代码是 5 位**（4 位 TSE 代码 + 1 位校验位），故匹配一律取前 4 位；
  按 4 位做等值比较会永远miss（2026-11-09 踩点教训）。

口坑：

- **保留窗口约 31 天**：更早日期的同一 URL 直接 404。跨财报季回溯取样本会全空，
  不是"没有披露"。故 golden 用**当日真实列表**做解析夹具（只锚结构，不锚日期/数值）。
- 同一 `code` 一天内可有多条披露（決算短信 + 業績予想修正 + 自己株式取得）——不去重。
- `XBRL` / `更新履歴` 两列常为空占位（`&nbsp;`），本单元不取值。
- 抓取失败即抛 ``RuntimeError``（失败即红，绝不造数）；单日 404 视为"该日无列表"跳过，
  但**若所有请求都失败**则抛错，避免把"全炸"伪装成"零披露"。
"""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

SOURCE = "jp-tdnet"
BASE_URL = "https://www.release.tdnet.info/inbs"
DEFAULT_CODES = ("7550", "7581", "2702", "3197")  # 日股餐饮宇宙（restaurant-universe.yaml）
TIMEOUT = 60  # 秒；单页 18–22KB，实测 <2s，60s 已是宽松上限

_ROW_RE = re.compile(r'<td class="oddnew-L kjTime"[^>]*>(.*?)</td>.*?'
                     r'<td class="oddnew-M kjCode"[^>]*>(.*?)</td>.*?'
                     r'<td class="oddnew-M kjName"[^>]*>(.*?)</td>.*?'
                     r'<td class="oddnew-M kjTitle"[^>]*>(.*?)</td>.*?'
                     r'<td class="oddnew-M kjPlace"[^>]*>(.*?)</td>', re.S)
_HREF_RE = re.compile(r'href="([^"]+)"')


def _clean(text: str) -> str:
    """去标签、压空白、解 `&nbsp;`。"""
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text.replace("&nbsp;", " ")).strip()


def _fetch(url: str) -> str:
    """取一页；网络层失败统一失败即红（``urllib`` 默认吃 ``HTTP(S)_PROXY`` 环境变量，
    海外/受限网络由调度环境提供代理，本单元不自带凭据）。"""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "ja,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{SOURCE}: HTTP {exc.code} {url}") from exc
    except Exception as exc:  # noqa: BLE001 - 连接层失败统一失败即红
        raise RuntimeError(f"{SOURCE}: 拉取失败 {type(exc).__name__}: {exc}") from exc
    for enc in ("utf-8", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def parse_listing(html: str, page_url: str, keep: set[str] | None, trade_date: str) -> list[dict]:
    """按结构解析一页列表，只保留 ``keep`` 里的 4 位代码。纯函数，golden 直接喂夹具。"""
    scraped_at = datetime.now(timezone.utc).isoformat()
    out: list[dict] = []
    for m in _ROW_RE.finditer(html or ""):
        time_s, code_s, name_s, title_html, place_s = m.groups()
        tdnet_code = _clean(code_s)
        ticker = tdnet_code[:4]
        if not tdnet_code or (keep is not None and ticker not in keep):
            continue
        href_match = _HREF_RE.search(title_html or "")
        doc_url = ""
        if href_match:
            href = href_match.group(1)
            doc_url = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('./')}"
        out.append({
            "trade_date": trade_date,
            "disclose_time": _clean(time_s),
            "tdnet_code": tdnet_code,
            "ticker": ticker,
            "company": _clean(name_s),
            "title": _clean(title_html),
            "exchange": _clean(place_s),
            "doc_url": doc_url,
            "source": SOURCE,
            "source_url": page_url,
            "scraped_at": scraped_at,
        })
    return out


def run_jp_tdnet(
    limit: int = 200,
    days: int = 7,
    codes: str | None = None,
    fixture: str | None = None,
) -> list[dict]:
    """走最近 ``days`` 天的日次列表，返回餐饮宇宙标的的披露行。

    ``fixture`` 直供时只解析该文件（golden 重放路径，零网络）。
    每一天的 404 视为"该日无列表"跳过；但若**全部请求都失败**则抛错，
    绝不把"全炸"伪装成"零披露"。
    """
    try:
        limit = int(limit)
        days = max(1, int(days))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{SOURCE}: limit/days 非法（{limit!r}/{days!r}）") from exc

    # codes=None → 默认餐饮宇宙；codes="" → **不过滤**（整页保留，用于车道首跑取数与验证）；
    # 其余按逗号切分。
    if codes is None:
        keep = set(DEFAULT_CODES)
    elif codes.strip() == "":
        keep = None  # 不过滤
    else:
        keep = {c.strip() for c in codes.split(",") if c.strip()}

    if fixture:
        html = open(fixture, encoding="utf-8", errors="replace").read()
        rows = parse_listing(html, f"file://{fixture}", keep, "fixture")
        print(f"{SOURCE}: fixture={fixture} rows={len(rows)}", file=sys.stderr)
        return rows[:limit]

    today = date.today()
    rows: list[dict] = []
    fetched = 0
    for i in range(days):
        day = today - timedelta(days=i)
        day_s = day.strftime("%Y%m%d")
        url = f"{BASE_URL}/I_list_001_{day_s}.html"
        try:
            html = _fetch(url)
        except RuntimeError as exc:
            if "HTTP 404" in str(exc):
                continue  # 该日无列表（或超出保留窗）
            raise
        fetched += 1
        rows.extend(parse_listing(html, url, keep, day.isoformat()))
    if fetched == 0:
        raise RuntimeError(f"{SOURCE}: {days} 天内一页都没取到（全失败或全 404，转人工）")
    print(f"{SOURCE}: pages={fetched} rows={len(rows)} codes={sorted(keep) if keep else 'ALL'}", file=sys.stderr)
    return rows[:limit]
