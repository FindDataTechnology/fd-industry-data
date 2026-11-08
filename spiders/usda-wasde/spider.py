"""spiders/usda-wasde —— USDA《World Agricultural Supply and Demand Estimates》月度供需平衡表。

数据表面（2026-11-08 实测）：

- 索引页 ``https://usda.gov/oce/commodity/wasde/``（302 到
  ``/about-usda/.../office-chief-economist/commodity-markets/wasde-report``）HTML 里
  直接列出每期的 ``.pdf / .txt / .xls / .xml`` 直链。
- 本单元只取 ``.txt``：定宽纯文本，标准库即可解析，不需要 xls 解析依赖，
  也不会把道歉页当工作簿打开。
- 期号命名 ``wasde{MM}{YY}``；正文首行 ``WASDE - 676 - 8   October 2026`` 给期号与
  发布月。实测 2026-11-08：``wasde1026.txt``（676，122KB）、``wasde0926.txt``
  （675，122KB）可取，``wasde0826/0726/0626.txt`` 均 404。

**最关键坑（必须实现，否则出脏数据）**

未发布期返回 **HTTP 200 + 369 字节 HTML**，内容跳
``https://www.usda.gov/please_wait/apology.htm``（文案 "This file is not yet
available"）。只看状态码会把错误页当数据解析。本单元三重判定：

1. ``Content-Type`` 必须含 ``text/plain``；
2. 响应体大小必须 ``>= MIN_BODY_BYTES``（实测正文 122KB，道歉页 369B）；
3. 正文必须匹配 ``WASDE - <n> - ...`` 期号行。

任一不满足即判「本期未发布」，计 ``unpublished``，不产出任何行。

**历史深度（如实声明）**：索引页只挂当前期与下一期，历史归档入口
``/oce/commodity-markets/wasde/historical-wasde-report-data`` 实测 **403**（Akamai）。
因此本单元按「最近 N 个自然月构造 URL」枚举（默认 6，遇 404/道歉页即跳过），
**当前真实可回溯窗口只有最近 2 期**，属短窗源，不假装有长序列。

只解析三类汇总表（Grains / Cotton / Oilseeds 的 ``World and U.S. Supply and Use``），
国家/分品种表形态不同、本单元不解析（见 README）。

只用标准库：``urllib`` + ``re`` + ``datetime``。
"""
from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

SOURCE = "usda-wasde"
BASE_URL = "https://www.usda.gov/oce/commodity/wasde/"
INDEX_URL = "https://usda.gov/oce/commodity/wasde/"
#: **历史期归档**（实测 2026-11-08）：`www.usda.gov/.../wasde{MM}{YY}.txt` 只留
#: 当期+上一期（18 个月里只有 2 期能取到，其余 403/404）。历史期在 USDA ESMIS
#: 归档页，且**同一 release-files 路径挂在 cornell 主机才 200、在 www.usda.gov 下 403**。
ESMIS_URL = "https://usda.library.cornell.edu/concern/publications/3t945q76s"
ESMIS_FILE_BASE = "https://usda.library.cornell.edu"
_RELEASE_FILE_RE = re.compile(
    r"/sites/default/release-files/\d+/wasde(\d{2})(\d{2})\.txt")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 60
#: 道歉页实测 369B，正文实测 122KB；阈值取中间，避免把道歉页当数据。
MIN_BODY_BYTES = 5000
#: 默认回溯月数（索引页只挂当前期与下一期，再往前是 403 归档页）
DEFAULT_MONTHS_BACK = 6

_ITEMS = ("output", "supply", "trade", "total_use", "ending_stocks")

_PAGE_MARK_RE = re.compile(r"WASDE\s*-\s*(\d+)\s*-\s*\d+\s+([A-Za-z]+)\s+(\d{4})")
_SECTION_RE = re.compile(r"World and U\.S\. Supply and Use for (.+?)\s+\d\s*/")
_UNIT_RE = re.compile(r"^\s*\(?\s*(Million[^()]*?)\s*\)?\s*$")
_SEASON_RE = re.compile(r"^\s+(\d{4}/\d{2})(?:\s*\((Est|Proj)\.\))?\s*(.*)$")
_FORECAST_RE = re.compile(
    r"^\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+([-\d.,\s]+)$"
)
_MONTH_NAMES = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11,
    "December": 12,
}
_HEADER_WORDS = {"commodity", "world and u.s. supply and use"}
#: 汇总表里的分区标签是固定三档（国家/分品种表的国名在表边界外已被截断）
_REGIONS = {"World", "Foreign", "United States"}
_FOOTNOTE_RE = re.compile(r"\s*\d+/\s*$")

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _build_opener() -> urllib.request.OpenerDirector:
    """海外直连；显式空代理表（不吃环境变量，避免本机代理劫持）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _fetch(opener: urllib.request.OpenerDirector, url: str) -> str | None:
    """取一个候选期；未发布 / 不存在 / 形态不符 → None（留痕，不抛）。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Referer": INDEX_URL}
    )
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            status = getattr(resp, "status", 200)
            ctype = (resp.headers.get("Content-Type") or "").lower()
            body = resp.read()
    except urllib.error.HTTPError as exc:
        logger.info("%s: HTTP %s — treated as not published", url, exc.code)
        return None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        logger.warning("%s: fetch failed (%s) — skip", url, exc)
        return None

    if status != 200:
        logger.info("%s: HTTP %s — skip", url, status)
        return None
    # 坑 1+2：未发布期是 200 + text/html 道歉页（实测 369B）
    if "text/plain" not in ctype:
        logger.info(
            "%s: content-type %r (likely apology page) — treated as not published",
            url, ctype,
        )
        return None
    if len(body) < MIN_BODY_BYTES:
        logger.info(
            "%s: body %d B < %d B (apology page) — treated as not published",
            url, len(body), MIN_BODY_BYTES,
        )
        return None
    text = body.decode("utf-8", errors="replace")
    # 坑 3：正文必须带期号行
    if not _PAGE_MARK_RE.search(text):
        logger.info("%s: no 'WASDE - <n>' header — not a report", url)
        return None
    return text


def _archive_issues(opener: urllib.request.OpenerDirector) -> list[tuple[str, int]]:
    """从 ESMIS 归档页取**历史期**的 .txt 直链（新→旧）。

    返回 ``(url, YYYYMM)``；取不到就返回空表（调用方回落到当期+上一期路径），
    不抛异常——归档页改版不该让整个单元挂掉，但要留日志。
    """
    req = urllib.request.Request(
        ESMIS_URL, headers={"User-Agent": USER_AGENT, "Referer": ESMIS_URL})
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        logger.warning("usda-wasde: archive page fetch failed (%s) — fallback", exc)
        return []

    out: dict[int, str] = {}
    for m in _RELEASE_FILE_RE.finditer(html):
        mm, yy = int(m.group(1)), int(m.group(2))
        ym = 2000 + yy, mm
        if not (1 <= mm <= 12):
            continue
        url = ESMIS_FILE_BASE + m.group(0)
        out[ym[0] * 100 + ym[1]] = url
    if not out:
        logger.warning("usda-wasde: archive page parsed 0 release files — fallback")
        return []
    issues = sorted(out.items(), key=lambda kv: kv[0], reverse=True)
    logger.info("usda-wasde: archive lists %d historical issue(s): %s … %s",
                len(issues), issues[-1][0], issues[0][0])
    return [(url, ym) for ym, url in issues]


def _candidate_urls(months_back: int, now: datetime) -> list[tuple[str, int]]:
    """最近 months_back 个自然月（含当月）的候选 URL，新→旧。"""
    out: list[tuple[str, int]] = []
    year, month = now.year, now.month
    for _ in range(max(months_back, 1)):
        out.append((f"{BASE_URL}wasde{month:02d}{year % 100:02d}.txt", year * 100 + month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return out


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------
def _to_float(token: str) -> float | None:
    token = token.strip()
    if not token or token in {"-", "—", "--", "NA", "N/A"}:
        return None
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def _clean_label(raw: str) -> str:
    label = _FOOTNOTE_RE.sub("", raw.strip())
    return re.sub(r"\s+", " ", label).strip()


def _numbers(rest: str) -> list[float | None]:
    return [_to_float(tok) for tok in rest.split()] if rest.strip() else []


def _emit(
    rows: list[dict],
    wasde_no: int,
    release_month: str,
    section: str,
    region: str,
    commodity: str,
    season: str,
    basis: str,
    forecast_month: str | None,
    values: list[float | None],
    unit: str,
    url: str,
    scraped_at: str,
) -> None:
    if not commodity or not values:
        return
    for item, value in zip(_ITEMS, values):
        if value is None:
            # 原文缺值（- / —）：不出行，绝不写 0
            continue
        rows.append(
            {
                "wasde_no": wasde_no,
                "release_month": release_month,
                "section": section,
                "region": region,
                "commodity": commodity,
                "season": season,
                "basis": basis,
                "forecast_month": forecast_month,
                "item": item,
                "value": value,
                "unit": unit,
                "source_url": url,
                "scraped_at": scraped_at,
            }
        )


def _parse_issue(text: str, url: str, scraped_at: str) -> tuple[int, str, list[dict]]:
    """解析一期 txt → (期号, 发布月 YYYY-MM, 行列表)。"""
    first = _PAGE_MARK_RE.search(text)
    if not first:
        return 0, "", []
    wasde_no = int(first.group(1))
    month = _MONTH_NAMES.get(first.group(2).capitalize())
    release_month = f"{int(first.group(3)):04d}-{month:02d}" if month else ""

    rows: list[dict] = []
    section = unit = region = commodity = season = basis = ""
    pending_unit = False
    # 汇总表的边界靠分隔线计数界定：标题后有 2 条 `====`（上/下框线）才进入数据区，
    # 数据区遇到下一条 `====` 即该表结束。否则会漏进后续「分品种/分国别」表，
    # 把 Beef / Argentina / "Region Beginning Produc-" 这类表头当成品类与地区。
    rules_seen = 0
    in_data = False

    for line in text.splitlines():
        if _PAGE_MARK_RE.search(line):
            continue

        sec = _SECTION_RE.search(line)
        if sec:
            section = _clean_label(sec.group(1).replace(", Continued", ""))
            region = commodity = season = basis = ""
            pending_unit = True
            rules_seen = 0
            in_data = False
            # 棉花表是单品类表（只有 World / United States / Foreign 三块），
            # 没有品类标签行，直接给品类名。
            if "Cotton" in section:
                commodity = "Cotton"
            continue

        if pending_unit:
            um = _UNIT_RE.match(line)
            if um:
                unit = um.group(1).strip()
                pending_unit = False
                continue
            if line.strip() and not set(line.strip()) <= set("= _-"):
                pending_unit = False

        stripped = line.strip()
        if not stripped:
            continue
        if set(stripped) <= set("= _-"):
            if not section:
                continue
            rules_seen += 1
            if rules_seen >= 2:
                if in_data:
                    # 汇总表下框线 → 本节结束，后续表格不再解析
                    section = unit = region = commodity = season = basis = ""
                    in_data = False
                else:
                    in_data = True
            continue
        if not in_data:
            continue

        fm = _FORECAST_RE.match(line)
        if fm and season:
            _emit(rows, wasde_no, release_month, section, region, commodity,
                  season, "Projection", fm.group(1), _numbers(fm.group(2)),
                  unit, url, scraped_at)
            continue

        sm = _SEASON_RE.match(line)
        if sm:
            season = sm.group(1)
            basis = {"Est": "Estimate", "Proj": "Projection"}.get(
                sm.group(2) or "", "Actual"
            )
            values = _numbers(sm.group(3))
            if values:
                _emit(rows, wasde_no, release_month, section, region, commodity,
                      season, basis, None, values, unit, url, scraped_at)
            continue

        if not section:
            continue

        # 无数字标签行：先剥脚注（"Total Grains 4/" → "Total Grains"）再判数字，
        # 否则带脚注的品类标签会被当成数字行丢掉，整块行被错挂在上一品类名下。
        label = _clean_label(stripped)
        if not label or label.lower() in _HEADER_WORDS:
            continue
        if any(ch.isdigit() for ch in label):
            continue
        # 地区用白名单判定（棉花表的 "World" 反而顶格，靠缩进判会误当品类）
        if label in _REGIONS:
            region = label
        else:
            commodity = label
    return wasde_no, release_month, rows


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def run_usda_wasde(
    limit: int = 200,
    window_start: str | None = None,
    window_end: str | None = None,
    months_back: int = DEFAULT_MONTHS_BACK,
) -> list[dict]:
    """取 WASDE 汇总供需平衡表（长表：期 × 分区 × 品类 × 季 × 指标）。

    ``limit`` 计行数；``window_start`` / ``window_end``（YYYY-MM 或 YYYY-MM-DD）
    按**发布月**过滤，先过滤后截断。
    """
    if (window_start is None) != (window_end is None):
        raise ValueError(
            f"usda-wasde: window_start/window_end 须成对出现，"
            f"got start={window_start!r} end={window_end!r}"
        )

    def _norm(value: str) -> str:
        value = value.strip()
        return value[:7] if len(value) >= 7 else value

    lo = _norm(window_start) if window_start else None
    hi = _norm(window_end) if window_end else None
    if lo and hi and lo > hi:
        raise ValueError(f"usda-wasde: window_start {lo} 晚于 window_end {hi}")

    opener = _build_opener()
    now = datetime.now(timezone.utc)
    scraped_at = now.isoformat()

    collected: list[dict] = []
    seen_issues: set[int] = set()
    fetched = unpublished = 0
    # 先跑归档（历史期），再跑当期+上一期路径；同一期只取一次（seen_issues 去重）。
    candidates = _archive_issues(opener) + _candidate_urls(months_back, now)
    for url, _ in candidates:
        text = _fetch(opener, url)
        if text is None:
            unpublished += 1
            continue
        wasde_no, release_month, rows = _parse_issue(text, url, scraped_at)
        if not rows or wasde_no in seen_issues:
            continue
        seen_issues.add(wasde_no)
        fetched += 1
        if lo and (not release_month or release_month < lo):
            continue
        if hi and (not release_month or release_month > hi):
            continue
        collected.extend(rows)

    if not collected:
        raise RuntimeError(
            f"usda-wasde: 0 rows parsed from {months_back} candidate month(s) "
            f"({fetched} issue(s) fetched, {unpublished} unpublished/missing)"
        )
    logger.info(
        "usda-wasde: %d rows from %d issue(s); %d candidate(s) unpublished/missing",
        len(collected), fetched, unpublished,
    )
    return collected[: max(int(limit), 0)] if limit is not None else collected


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for row in run_usda_wasde(limit=10):
        print(row)
