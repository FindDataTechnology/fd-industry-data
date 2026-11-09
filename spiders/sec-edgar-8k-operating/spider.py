"""sec-edgar-8k-operating — 美股酒店 8-K 业绩新闻稿（exhibit 99.1）车道.

数据面（全部官方、免 key；见 ``openspec/changes/add-hotel-vertical/reports/4.0-wave3-recon.md`` §3）:

- 事件面：``GET https://data.sec.gov/submissions/CIK##########.json`` 的
  ``filings.recent`` —— 逐条含 ``form / items / accessionNumber / filingDate /
  reportDate / primaryDocument``。只收 ``form=8-K`` 且 ``items`` 含 ``2.02``
  （Results of Operations and Financial Condition）的申报。
- 目录面：``GET https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json``
  —— 文件清单 + 字节数（**没有 exhibit 类型字段**：``type`` 是 MIME 图标名，
  实测 ``text.gif``，据此判 99.1 是错的）。
- 选件面：8-K 正文本身列出 exhibit 清单，链接文本写明「Exhibit 99.1 —
  Press Release …」。本单元在正文里按**上下文文本**选件，不按文件名猜。

## 为什么必须选件而不是取 ``primaryDocument``

实测（2026-11-08，5 家）：

| ticker | 8-K 正文里的 exhibit 文件名 | exhibit 字节 |
|---|---|---|
| MAR | ``mar-2026q3xex99earningsrel.htm`` | 1,273,792 |
| HLT | ``q32026earningsrelease.htm`` | 621,354 |
| WH  | ``exhibit9912026930.htm`` | 659,112 |
| H  | ``q32026earningsrelease.htm``（另有 ``…supplementald.htm`` 演示稿） | 791,879 |
| CHH | ``exhibit991earningspressrel.htm`` | 577,355 |

五家五种命名，且 H 的同一份 8-K 里还有一份 7.01 的演示稿 —— 按「目录里唯一的
.htm」或按文件名正则都会选错。选件规则见 ``pick_exhibit``：先按链接上下文
（``exhibit 99.1`` / ``press release`` / ``results``）收窄，再排除
``supplemental`` / ``presentation``（7.01 演示稿），最后要求**唯一命中**；
命中不唯一即返回 ``ambiguous-exhibit-link``，不猜。

## 网络纪律（实测坑）

本机 ``python urllib`` 直连 SEC 的 1.27 MB exhibit 会卡死（90 秒只收 128 KB），
``curl --compressed --http1.1`` 同 URL 1.3 秒完成。因此本单元**所有**请求都带
``Accept-Encoding: gzip`` 并本地解压，超时 90 秒，传输类失败重试一次，
HTTP 状态错误不重试（403 = User-Agent 缺联系邮箱，是配置错不是抖动）。

## 输出两态（``status``，全部如实呈现）

- ``ok`` —— 已定位 exhibit 99.1（``exhibit_url`` 可直接取）
- ``no-exhibit-link`` —— 8-K 正文里没有指向 exhibit 的文件链接（实测 HGV）
- ``ambiguous-exhibit-link`` —— 多个候选且规则无法唯一确定

本单元**不解析** exhibit 正文：正文解析在
``scripts/hotel_extractors/sec_8k_operating.py``（逐家 mapping，任务 4.3）。

## 边界注记（spec 强制，逐字）

exhibit 99.1 是业绩新闻稿，不是财报正文；XBRL 结构化事实仍以 companyfacts 为准，
而 companyfacts 只返回**无维度**事实（分部/地区维度不在其中）。逐家 exhibit 文件名与
表格形态差异极大（MAR 是 ``*ex99earningsrel.htm`` 的品牌表，其他公司多为分地区
汇总表或仅正文叙述），因此 **逐家 mapping 表 + 无 mapping 即显式不可得**，
不允许按列序猜测。
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from html import unescape
from pathlib import Path

logger = logging.getLogger("sec-edgar-8k-operating")

SOURCE = "sec-edgar-8k-operating"

#: SEC 要求 User-Agent 带应用名 + 联系邮箱；缺了 data.sec.gov 直接 403（实测）
USER_AGENT = "HotelVertical research@finddatatech.cloud"

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/index.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{name}"

REQUEST_PAUSE_SECONDS = 0.15     # SEC 明示 <=10 req/s；串行 + 0.15s 远低于上限
REQUEST_TIMEOUT_SECONDS = 90
EARNINGS_ITEM = "2.02"

#: 车道默认宇宙 = 注册里 lanes 含本车道的美股酒店（docs/hotel-universe.yaml）。
#: MUST NOT 在源码里写死公司列表：新增实体只改宇宙真源。
HOTEL_UNIVERSE_ENV = "FD_HOTEL_UNIVERSE"
UNIVERSE_RELPATH = Path("docs") / "hotel-universe.yaml"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ANCHOR_RE = re.compile(
    r"""<a\b[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""",
    re.I | re.S,
)

#: exhibit 99.1 的上下文特征（大小写无关）。「press release」是 SEC 8-K 里
#: 业绩新闻稿的标准描述语；「results」覆盖少数只写 results 的写法。
_EXHIBIT_HINT = re.compile(r"exhibit\s*99\.1|press\s+release|earnings|results", re.I)
#: 7.01 演示稿/补充材料不是业绩新闻稿：出现在同一份 8-K 里（实测 H）
_NOT_EARNINGS = re.compile(r"supplemental|presentation|slide", re.I)
#: 「Exhibit 99.1」字样：最强证据，优先级高于关键词
_EXPLICIT_991 = re.compile(r"exhibit\s*99\.1", re.I)

#: 财季末日期：8-K 正文固定写「quarter ended September 30, 2026」。
#: **不要用 submissions 的 ``reportDate`` 当财季末** —— 实测 MAR 2026Q3 的
#: 8-K 其 reportDate = 2026-11-02（最早事件日＝申报日），不是 2026-09-30。
_QUARTER_ENDED = re.compile(
    r"quarter\s+ended\s+([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})", re.I
)
_MONTHS = {name: index for index, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"), start=1)}


def period_end_from_text(text: str) -> str | None:
    """从 8-K 正文/新闻稿取财季末（``quarter ended <Month> <D>, <YYYY>``）。"""
    match = _QUARTER_ENDED.search(_plain(text))
    if not match:
        return None
    month = _MONTHS.get(match.group(1).lower())
    if month is None:
        return None
    return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(2)):02d}"


def _find_universe_path() -> Path:
    """定位酒店宇宙真源（``docs/hotel-universe.yaml``）。

    顺序：``$FD_HOTEL_UNIVERSE`` → 从本文件向上找到第一个含该文件的祖先目录。
    两条路都不通即硬失败 —— 车道默认宇宙的正确性比可用性重要（不静默回落）。
    """
    override = os.environ.get(HOTEL_UNIVERSE_ENV)
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise RuntimeError(
                f"{SOURCE}: {HOTEL_UNIVERSE_ENV}={override} 不是文件"
            )
        return path
    for parent in Path(__file__).resolve().parents:
        candidate = parent / UNIVERSE_RELPATH
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        f"{SOURCE}: 找不到酒店宇宙真源 {UNIVERSE_RELPATH}"
        "（可用 $FD_HOTEL_UNIVERSE 指定）"
    )


@lru_cache(maxsize=1)
def _default_tickers() -> tuple[str, ...]:
    """注册表驱动默认宇宙：region=US 且 lanes 含本车道的实体（顺序即注册表顺序）。"""
    import yaml

    path = _find_universe_path()
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[str] = []
    for company in doc.get("companies") or []:
        if str(company.get("region") or "").upper() != "US":
            continue
        lanes = company.get("lanes") or []
        if SOURCE not in lanes:
            continue
        code = str(company.get("code") or "").strip()
        if code:
            out.append(code.upper())
    if not out:
        raise RuntimeError(
            f"{SOURCE}: 宇宙真源 {path} 里没有 region=US 且 lanes 含 {SOURCE} 的实体"
            "——先登记车道可达性（任务 3.6 / 4.3）"
        )
    return tuple(out)


def _decompress(raw: bytes, encoding: str | None) -> bytes:
    if (encoding or "").lower() == "gzip":
        try:
            return gzip.decompress(raw)
        except OSError:
            # 少数边缘代理会声明 gzip 却发明文；明文照用，不让解压失败吞掉内容
            return raw
    return raw


def _get(url: str, what: str, accept: str = "text/html,application/json;q=0.9,*/*;q=0.8") -> bytes:
    """GET + gzip 解压；传输失败重试一次，HTTP 错误立刻红跑。

    python urllib 不带 ``Accept-Encoding: gzip`` 在 1 MB 级文件上会卡死（实测），
    所以这个请求头是本单元的硬要求，不是优化项。
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Encoding": "gzip",
        },
    )
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:  # noqa: S310
                return _decompress(resp.read(), resp.headers.get("Content-Encoding"))
        except urllib.error.HTTPError as exc:
            hint = ""
            if exc.code == 403:
                hint = "（SEC 拒绝：User-Agent 必须带应用名 + 联系邮箱）"
            raise RuntimeError(
                f"{SOURCE}: HTTP {exc.code} for {what} ({url}){hint}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 — 传输类失败：重试一次
            last_error = exc
            logger.warning("fetch %s attempt %d failed: %s", what, attempt, exc)
            if attempt == 1:
                time.sleep(1.0)
    raise RuntimeError(f"{SOURCE}: 无法获取 {what} ({url}): {last_error}") from last_error


def _get_json(url: str, what: str) -> dict:
    raw = _get(url, what, accept="application/json")
    try:
        doc = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"{SOURCE}: {what} 不是合法 JSON ({url}): {exc}") from exc
    if not isinstance(doc, dict):
        raise RuntimeError(f"{SOURCE}: {what} 载荷不是 JSON 对象 ({url})")
    return doc


def _load_cik_map() -> dict[str, str]:
    """ticker -> 10 位补零 CIK（SEC company_tickers.json）。"""
    doc = _get_json(TICKER_MAP_URL, "CIK ticker map")
    mapping: dict[str, str] = {}
    for value in doc.values():
        if not isinstance(value, dict):
            continue
        ticker, cik = value.get("ticker"), value.get("cik_str")
        if not ticker or cik is None:
            continue
        try:
            mapping[str(ticker).strip().upper()] = f"{int(cik):010d}"
        except (TypeError, ValueError):
            continue
    if not mapping:
        raise RuntimeError(f"{SOURCE}: CIK 映射 {TICKER_MAP_URL} 无 ticker 条目")
    return mapping


def _coerce_tickers(tickers) -> tuple[str, ...]:
    if tickers is None:
        return _default_tickers()
    if isinstance(tickers, str):
        parts = [p.strip() for p in tickers.split(",")]
    elif isinstance(tickers, (list, tuple, set)):
        parts = [str(p).strip() for p in tickers]
    else:
        raise ValueError(
            f"{SOURCE}: tickers 必须是 list/tuple 或逗号分隔字符串，"
            f"实际 {type(tickers).__name__}"
        )
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part:
            continue
        ticker = part.upper()
        if ticker not in seen:
            seen.add(ticker)
            out.append(ticker)
    if not out:
        raise ValueError(f"{SOURCE}: tickers 解析为空列表")
    return tuple(out)


def _plain(fragment: str) -> str:
    """HTML 片段 -> 纯文本（去标签、反转义实体、压空白）。"""
    return _WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", fragment or ""))).strip()


def exhibits(primary_html: str) -> list[dict]:
    """8-K 正文里的同目录文件链接 + 其上下文文本（选件证据）。

    返回 ``[{"name", "context"}]``，只保留**同目录相对文件名**（不含 ``/``，
    且不是 ``.xsd`` 架构件）——绝对 URL 与外站链接不是 exhibit。
    """
    text_without_tags = _WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", primary_html or "")))
    if not text_without_tags:  # pragma: no cover - 防御空文档
        return []
    seen: dict[str, str] = {}
    for match in _ANCHOR_RE.finditer(primary_html or ""):
        href = unescape(match.group(1) or "").strip()
        if not href or "/" in href or href.lower().endswith(".xsd"):
            continue
        name = href.split("#")[0].strip()
        if not name:
            continue
        # 上下文取锚点出现的字符位置向前后各 320 字符：8-K 的 exhibit 清单
        # 是「99.1 — Press Release …」这种相邻写法，窗口足够且不至于跨越到
        # 别的 exhibit 段落之外（实测最长描述 210 字符）
        start = primary_html.find(match.group(0))
        before = _plain(primary_html[max(0, start - 600):max(0, start)])
        after = _plain(primary_html[start:start + 900])
        context = f"{before} {after}".strip()
        if name not in seen or len(context) > len(seen[name]):
            seen[name] = context
    return [{"name": name, "context": ctx} for name, ctx in seen.items()]


def pick_exhibit(candidates: list[dict]) -> tuple[dict | None, str]:
    """从候选里选业绩新闻稿 exhibit。

    规则（顺序固定，全部基于正文文本证据，不按文件名猜）:

    1. 先排除上下文含 ``supplemental / presentation / slide`` 的 7.01 演示稿；
    2. 显式写着 ``Exhibit 99.1`` 的候选优先；
    3. 否则按 ``press release / earnings / results`` 关键词收窄；
    4. 只有**唯一**命中才返回；0 个命中返回 ``never``，多个命中返回 ``ambiguous``。
    """
    if not candidates:
        return None, "no-candidates"
    filtered = [c for c in candidates if not _NOT_EARNINGS.search(c["context"])]
    pool = filtered or candidates
    explicit = [c for c in pool if _EXPLICIT_991.search(c["context"])]
    if len(explicit) == 1:
        return explicit[0], "explicit-exhibit-99.1"
    if len(explicit) > 1:
        return None, "ambiguous-explicit-99.1"
    hinted = [c for c in pool if _EXHIBIT_HINT.search(c["context"])]
    if len(hinted) == 1:
        return hinted[0], "press-release-context"
    if not hinted:
        return None, "no-exhibit-link"
    return None, "ambiguous-exhibit-link"


def _filing_index(cik10: str, accession: str) -> dict[str, int]:
    """filing 目录清单 name -> 字节数（空串按 0）。"""
    url = INDEX_URL.format(cik_int=int(cik10), acc_nodash=accession.replace("-", ""))
    doc = _get_json(url, f"filing index {accession}")
    directory = doc.get("directory")
    if not isinstance(directory, dict):
        raise RuntimeError(f"{SOURCE}: filing index {accession} 无 'directory' 块")
    items = directory.get("item")
    if not isinstance(items, list):
        raise RuntimeError(f"{SOURCE}: filing index {accession} 无 'item' 列表")
    sizes: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        try:
            sizes[str(name)] = int(str(item.get("size") or "0").strip() or 0)
        except ValueError:
            sizes[str(name)] = 0
    return sizes


def _earnings_filings(submissions: dict, ticker: str, filings_back: int) -> list[dict]:
    """8-K 且 items 含 2.02 的最近 N 份申报（按申报日倒序）。"""
    filings = submissions.get("filings")
    if not isinstance(filings, dict):
        raise RuntimeError(f"{SOURCE}: {ticker} submissions 无 'filings' 块")
    recent = filings.get("recent")
    if not isinstance(recent, dict):
        raise RuntimeError(f"{SOURCE}: {ticker} submissions 无 'filings.recent'")
    columns = ("form", "items", "accessionNumber", "filingDate", "reportDate",
               "primaryDocument")
    arrays = {name: recent.get(name) for name in columns}
    for name, arr in arrays.items():
        if not isinstance(arr, list):
            raise RuntimeError(f"{SOURCE}: {ticker} filings.recent 缺 {name}")
    lengths = {len(arr) for arr in arrays.values()}
    if len(lengths) != 1:
        raise RuntimeError(
            f"{SOURCE}: {ticker} filings.recent 数组长度不齐 {sorted(lengths)}"
        )

    out: list[dict] = []
    for i in range(lengths.pop()):
        if arrays["form"][i] != "8-K":
            continue
        items = str(arrays["items"][i] or "")
        if EARNINGS_ITEM not in items:
            continue
        out.append(
            {
                "accession": arrays["accessionNumber"][i],
                "filed": arrays["filingDate"][i] or None,
                # submissions 的 reportDate 是「最早事件日」，8-K 上多数等于申报日，
                # **不是财季末**（财季末由 period_end_from_text 从正文取）
                "event_date": arrays["reportDate"][i] or None,
                "items": items,
                "primary_document": arrays["primaryDocument"][i] or None,
            }
        )
        if len(out) >= filings_back:
            break
    return out


def run_sec_edgar_8k_operating(
    limit: int = 80,
    tickers=None,
    filings_back: int = 4,
) -> list[dict]:
    """Entry point for fd-runner (spiders/sec-edgar-8k-operating).

    Args:
        limit: 最大返回行数（一行 = 一份 8-K 业绩申报及其 exhibit 指针）。
        tickers: 公司宇宙（JSON 数组或逗号分隔字符串）；缺省 = 酒店宇宙真源里
            region=US 且 lanes 含本车道的实体。
        filings_back: 每家公司回看几份 8-K 业绩申报（默认 4 = 约一年季报）。

    Returns:
        每份申报一行；``status`` 说明 exhibit 选件结果（``ok`` /
        ``no-exhibit-link`` / ``ambiguous-*``）。任何抓取/解析/CIK 失败抛
        RuntimeError（红跑）；零行同样算失败。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"{SOURCE}: limit 必须是 int，实际 {limit!r}") from None
    if limit <= 0:
        return []
    try:
        filings_back = int(filings_back)
    except (TypeError, ValueError):
        raise ValueError(
            f"{SOURCE}: filings_back 必须是 int，实际 {filings_back!r}"
        ) from None
    if filings_back <= 0:
        raise ValueError(f"{SOURCE}: filings_back 必须 >= 1")

    ticker_list = _coerce_tickers(tickers)

    try:
        from fd_industry_data.cancel_event import is_set as cancel_set
    except ImportError:  # 仓外独立运行

        def cancel_set() -> bool:
            return False

    scraped_at = datetime.now(timezone.utc).isoformat()
    cik_map = _load_cik_map()

    rows: list[dict] = []
    for ticker in ticker_list:
        if cancel_set():
            logger.info("cancel requested; stopping at %s", ticker)
            break
        cik10 = cik_map.get(ticker)
        if not cik10:
            raise RuntimeError(
                f"{SOURCE}: ticker {ticker} 在 {TICKER_MAP_URL} 里无法解析"
            )
        submissions = _get_json(SUBMISSIONS_URL.format(cik10=cik10), f"submissions {ticker}")
        time.sleep(REQUEST_PAUSE_SECONDS)
        filings = _earnings_filings(submissions, ticker, filings_back)
        if not filings:
            logger.warning("%s: 近期无 items 含 2.02 的 8-K", ticker)

        for filing in filings:
            accession = filing["accession"]
            if not accession:
                continue
            sizes = _filing_index(cik10, accession)
            time.sleep(REQUEST_PAUSE_SECONDS)
            primary = filing["primary_document"]
            base = DOC_URL.format(
                cik_int=int(cik10), acc_nodash=accession.replace("-", ""), name=""
            )
            status, picked, evidence, exhibit_url = "no-primary-document", None, "", None
            period_end = None
            if primary and primary in sizes:
                primary_url = base + primary
                body = _get(primary_url, f"8-K primary {ticker} {accession}")
                time.sleep(REQUEST_PAUSE_SECONDS)
                text = body.decode("utf-8", errors="replace")
                period_end = period_end_from_text(text)
                candidate, evidence = pick_exhibit(exhibits(text))
                if candidate is None:
                    status = evidence
                elif candidate["name"] not in sizes:
                    # 正文链接到目录里不存在的文件 = 上游目录与正文不一致，不猜
                    status = "exhibit-missing-from-index"
                else:
                    picked = candidate["name"]
                    exhibit_url = base + picked
                    status = "ok"
            row = {
                "ticker": ticker,
                "cik": cik10,
                "accession": accession,
                "filed": filing["filed"],
                "period_end": period_end,
                "event_date": filing["event_date"],
                "items": filing["items"],
                "primary_doc_url": base + primary if primary else None,
                "exhibit_filename": picked,
                "exhibit_url": exhibit_url,
                "exhibit_bytes": sizes.get(picked) if picked else None,
                "status": status,
                "pick_evidence": evidence,
                "source": SOURCE,
                "scraped_at": scraped_at,
            }
            rows.append(row)
            if len(rows) >= limit:
                break
        if len(rows) >= limit:
            break

    if not rows:
        raise RuntimeError(
            f"{SOURCE}: {list(ticker_list)} 零行（filings_back={filings_back}）"
            "——拒绝以空结果报成功"
        )
    ok = sum(1 for r in rows if r["status"] == "ok")
    logger.info(
        "%s: %d 行（exhibit 定位成功 %d / 失败 %d）覆盖 %d 家",
        SOURCE, len(rows), ok, len(rows) - ok, len({r["ticker"] for r in rows}),
    )
    return rows[:limit]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for row in run_sec_edgar_8k_operating(limit=10):
        print(row)
