"""spiders/jp-foodservice-trend —— 日本フードサービス協会「外食産業市場動向調査」月次 Excel。

数据表面（2026-11-07 实测）：

- 索引页 ``https://www.jfnet.or.jp/industry_report/``（第 N 页 ``/industry_report/page/N/``）
  每页 9 份月次报告，每份挂 PDF + **Excel** 两个链接；
- Excel 直链形如 ``https://www.jfnet.or.jp/wp/wp-content/uploads/<YYYY>/<MM>/data_c_m<YYYY>_<M>.xls``
  （个别月份是 ``.xlsx``，如 2023-08；扩展名与目录月不可推导，必须从索引页取）；
- 工作簿两张表：``全店``（数据）+ ``概要_公表版``（调查方法/样本量，不取）。

``全店`` 表布局（2022-12 与 2026-09 两版实测一致）：

```
行 67  ａ．売上高前年同月比        ← 块头（指标）
行 69  25年9月 10月 … 26年9月      ← 月份行（首列带年、后续只写月，跨年需推进）
行 70  全　　体  1.0481 … 1.0731   ← 7 个业态各一行
行 78  ｂ．店舗数前年同月比
行 89  ｃ．利用客数前年同月比
行 100 ｄ．客単価前年同月比
```

块与月份列一律**按内容定位**（不写死行列号）：块头按「前年同月比 + 指标关键词」识别，
月份列按「N年M月 / M月」标签识别，业态行按首列标签匹配 7 个规范名。

口径事实（上游原文）：

- 指标是**前年同月比**（``概要_公表版`` 3）：「回答事業社数をベースにした前年同月比を指標とする」，
  故值为 ratio（1.0 = 与去年同月持平），本单元**原样输出该 ratio**，不换算成增减率、不重算；
- 「全店データ」= 含新开店的全部店铺（非既存店口径），全体为各业态合计；
- 每份报告只带 **13 个月滚动窗**（当月 + 前 12 个月），所以单份文件不足以覆盖历史；
  更深历史靠索引页归档（月度 Excel 可回溯到第 8 页，约 2020 年；第 9 页起为年度报告）。

去重：同一 (month, segment) 可能被多份报告的滚动窗覆盖——取**最新报告版**的值
（``edition`` 记录胜出版本），这样同一月份的值在后续重跑中稳定可复现。

工单：``data-source-scouting/restaurant/jp-foodservice-trend-scouting.md``（日文源，见该记录）。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from fd_industry_data import tabular

SOURCE = "jp-foodservice-trend"
BASE_URL = "https://www.jfnet.or.jp"
INDEX_URL = f"{BASE_URL}/industry_report/"
INDEX_PAGE_URL = BASE_URL + "/industry_report/page/{page}/"
PROXY = "http://127.0.0.1:7890"
TIMEOUT = 90
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

#: 上游业态名（首列为全角空格填充，读取时先规范化）。输出用 ASCII 斜杠形式。
SEGMENTS: tuple[str, ...] = (
    "全体",
    "ファーストフード",
    "ファミリーレストラン",
    "パブレストラン/居酒屋",
    "ディナーレストラン",
    "喫茶",
    "その他",
)
_SEGMENT_KEYS = {s: i for i, s in enumerate(SEGMENTS)}

#: 块头 → 列名。顺序即匹配优先级：「客単価」必须先于「客数」（子串关系）。
_METRICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("spend_per_customer_yoy", ("客単価",)),
    ("customer_count_yoy", ("利用客数", "客数")),
    ("store_count_yoy", ("店舗数",)),
    ("sales_yoy", ("売上高",)),
)

_REPORT_RE = re.compile(r"data_c_m(\d{4})_(\d{1,2})\.(?:xls|xlsx)$", re.I)
_YEAR_MONTH_RE = re.compile(r"^(\d{2,4})年(\d{1,2})月$")
_MONTH_ONLY_RE = re.compile(r"^(\d{1,2})月$")
_ANCHOR_RE = re.compile(r"""<a\b[^>]*\bhref=["']([^"']+)["']""", re.I)

#: 前年同月比合理域（ratio）。越界=版面错位或口径变更，计异常而非造数。
_VALUE_MIN, _VALUE_MAX = 0.2, 5.0
#: 每块至少解析出的格数；低于此值判定为版面漂移（失败即红）。
_MIN_CELLS_PER_BLOCK = 10
_MAX_PAGES = 12


# --------------------------------------------------------------------------- io


def _http_get(url: str, *, timeout: int = TIMEOUT, proxy_retry: bool = True) -> bytes:
    """GET 原始字节；直连失败经本地代理重试一次（每 URL 至多 2 次）。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 固定 https 主机
            return resp.read()
    except Exception as exc:  # noqa: BLE001 - 直连失败转代理兜底
        first = exc
    if not proxy_retry:
        raise RuntimeError(f"{SOURCE}: 拉取失败 {url}: {type(first).__name__}: {first}") from first
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
    )
    try:
        with opener.open(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"{SOURCE}: 拉取失败（直连 {type(first).__name__} + 代理 {type(exc).__name__}）: {url}"
        ) from exc


# ------------------------------------------------------------------- index page


def _norm(text: str) -> str:
    """去掉所有空白（含全角空格），全角斜杠归一到 ASCII。"""
    return re.sub(r"\s+", "", str(text or "")).replace("／", "/")


def _index_entries(html: str) -> list[tuple[tuple[int, int], str]]:
    """索引页 → [(edition(年,月), excel_url)]，按版次降序（新→旧）。"""
    found: dict[tuple[int, int], str] = {}
    for href in _ANCHOR_RE.findall(html):
        url = urllib.parse.urljoin(BASE_URL, href.strip())
        m = _REPORT_RE.search(urllib.parse.urlparse(url).path)
        if not m:
            continue
        key = (int(m.group(1)), int(m.group(2)))
        if not 1 <= key[1] <= 12:
            continue
        found.setdefault(key, url)
    return sorted(found.items(), key=lambda kv: kv[0], reverse=True)


def _list_reports(since: tuple[int, int] | None) -> list[tuple[tuple[int, int], str]]:
    """列出要下载的月次报告。

    ``since`` 为 None 时只取第 1 页（9 份最新报告，滚动窗并集约 21 个月）；
    否则逐页回溯到版次早于 ``since`` 为止，只保留 edition >= since 的报告。
    """
    wanted: dict[tuple[int, int], str] = {}
    for page in range(1, _MAX_PAGES + 1):
        url = INDEX_URL if page == 1 else INDEX_PAGE_URL.format(page=page)
        entries = _index_entries(_http_get(url).decode("utf-8", "replace"))
        for key, xls_url in entries:
            wanted.setdefault(key, xls_url)
        if since is None:
            break
        if not entries:
            break  # 该页只有年度报告（page 9+ 实测如此）
        if min(k for k, _ in entries) <= since:
            break
    reports = sorted(wanted.items(), key=lambda kv: kv[0], reverse=True)
    if since is not None:
        reports = [r for r in reports if r[0] >= since]
    return reports


# ------------------------------------------------------------------ xls parsing


def _month_label_columns(row: list[str]) -> dict[int, tuple[int, int]]:
    """月份行 → {列号: (年, 月)}；无月份标签时返回空 dict。"""
    cols: dict[int, tuple[int, int]] = {}
    year: int | None = None
    cur_month: int | None = None
    for idx, cell in enumerate(row):
        label = _norm(cell)
        if not label:
            continue
        if m := _YEAR_MONTH_RE.match(label):
            y = int(m.group(1))
            year = y + 2000 if y < 100 else y
            cur_month = int(m.group(2))
            cols[idx] = (year, cur_month)
        elif m := _MONTH_ONLY_RE.match(label):
            if year is None or cur_month is None:
                continue
            month = int(m.group(1))
            if month <= cur_month:  # 跨年
                year += 1
            cur_month = month
            cols[idx] = (year, month)
    return cols if len(cols) >= 2 else {}


def _metric_of(text: str) -> str | None:
    if "前年同月比" not in text:
        return None
    for metric, keywords in _METRICS:
        if any(k in text for k in keywords):
            return metric
    return None


def _num(text: str) -> float | None:
    raw = _norm(text)
    if not raw or raw in ("-", "－", "…", "X", "x", "na"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_report(
    data: bytes,
    edition: tuple[int, int],
    url: str,
    *,
    skipped: list[str],
) -> dict[tuple[str, str], dict]:
    """单份工作簿 → {(month, segment): {metric: value}}（附 edition/url）。"""
    grid = tabular.read_grid(data, sheet="全店")
    if not grid:
        raise RuntimeError(f"{SOURCE}: {url} 的「全店」表为空（上游结构变更）")

    # 块头：任意前 3 列命中「前年同月比 + 指标」的行
    headers: list[tuple[int, str]] = []
    for r, row in enumerate(grid):
        for cell in row[:3]:
            metric = _metric_of(_norm(cell))
            if metric:
                headers.append((r, metric))
                break
    if not headers:
        raise RuntimeError(f"{SOURCE}: {url} 未找到前年同月比区块（版面变更）")

    out: dict[tuple[str, str], dict] = {}
    for i, (start, metric) in enumerate(headers):
        end = headers[i + 1][0] if i + 1 < len(headers) else len(grid)
        date_cols: dict[int, tuple[int, int]] = {}
        date_row = -1
        for r in range(start + 1, end):
            date_cols = _month_label_columns(grid[r])
            if date_cols:
                date_row = r
                break
        if date_row < 0:
            raise RuntimeError(f"{SOURCE}: {url} 区块 {metric} 缺月份行（版面变更）")

        cells = 0
        for r in range(date_row + 1, end):
            row = grid[r]
            label = _norm(row[0]) if row else ""
            if label not in _SEGMENT_KEYS:
                continue
            for col, (year, month) in date_cols.items():
                raw = row[col] if col < len(row) else ""
                value = _num(raw)
                if value is None:
                    continue
                if not _VALUE_MIN <= value <= _VALUE_MAX:
                    skipped.append(f"{year}-{month:02d} {label} {metric}={value!r}")
                    continue
                cells += 1
                key = (f"{year}-{month:02d}", label)
                item = out.setdefault(key, {"metrics": {}, "edition": edition, "url": url})
                item["metrics"][metric] = value
        if cells < _MIN_CELLS_PER_BLOCK:
            raise RuntimeError(
                f"{SOURCE}: {url} 区块 {metric} 仅解析 {cells} 格（版面漂移，转人工）"
            )
    return out


# ----------------------------------------------------------------------- public


def _parse_since(since: str | None) -> tuple[int, int] | None:
    if since is None:
        return None
    m = re.match(r"^(\d{4})-(\d{1,2})$", str(since).strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError(f"since must be 'YYYY-MM', got {since!r}")
    return int(m.group(1)), int(m.group(2))


def run_jp_foodservice_trend(
    limit: int = 500,
    since: str | None = None,
) -> list[dict]:
    """取「外食産業市場動向調査」全店月次（业态 × 四个前年同月比指标）。

    Args:
        limit: 最大返回行数（= 月份 × 业态），按「月份升序、业态按上游顺序」截断。
        since: 最早月份 ``YYYY-MM``（含）。给定时逐页回溯索引并下载覆盖该月及以后
            的每份报告（滚动窗去重取最新版）；缺省只取第 1 页（最新 9 份报告）。

    Raises:
        RuntimeError: 索引页/工作簿取数失败、版面漂移、或 0 数据行（失败即红）。
        ValueError: 参数非法。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    since_key = _parse_since(since)
    if limit <= 0:
        return []

    reports = _list_reports(since_key)
    if not reports:
        raise RuntimeError(f"{SOURCE}: 索引页无月次 Excel 链接（上游结构变更，转人工）")
    print(
        f"{SOURCE}: {len(reports)} 份月次报告（{reports[-1][0][0]}-{reports[-1][0][1]:02d}"
        f" … {reports[0][0][0]}-{reports[0][0][1]:02d}）",
        file=sys.stderr,
    )

    merged: dict[tuple[str, str], dict] = {}
    skipped: list[str] = []
    scraped_at = datetime.now(timezone.utc).isoformat()
    for edition, url in reports:  # 新→旧；旧版不覆盖新版
        parsed = _parse_report(_http_get(url), edition, url, skipped=skipped)
        for key, item in parsed.items():
            if key not in merged or edition > merged[key]["edition"]:
                merged[key] = item

    rows: list[dict] = []
    for (month, segment), item in sorted(
        merged.items(), key=lambda kv: (kv[0][0], _SEGMENT_KEYS[kv[0][1]])
    ):
        if since_key is not None and month < f"{since_key[0]}-{since_key[1]:02d}":
            continue  # since 语义 = 最早输出月份（封面报告滚动窗会多带前 12 个月）
        row = {
            "month": month,
            "segment": segment,
            "sales_yoy": None,
            "store_count_yoy": None,
            "customer_count_yoy": None,
            "spend_per_customer_yoy": None,
            "edition": f"{item['edition'][0]}-{item['edition'][1]:02d}",
            "url": item["url"],
            "source": SOURCE,
            "scraped_at": scraped_at,
        }
        for metric, value in item["metrics"].items():
            row[metric] = value
        # 跨块恒等式守卫：上游口径下 売上高比 = 利用客数比 × 客単価比（实测两版工作簿
        # 全格偏差 ≤ 4.5e-16）。只可能被「块-列错位」破坏，故偏差超 1e-6 判红转人工。
        s, c, p = row["sales_yoy"], row["customer_count_yoy"], row["spend_per_customer_yoy"]
        if s and c and p and abs(c * p / s - 1) > 1e-6:
            raise RuntimeError(
                f"{SOURCE}: {month} {segment} 指标恒等式失配 "
                f"({c}×{p}≠{s})，疑似区块/列错位，转人工"
            )
        rows.append(row)

    if not rows:
        raise RuntimeError(f"{SOURCE}: 解析结果 0 行（上游口径变更，转人工）")
    if skipped:
        print(
            f"{SOURCE}: 跳过 {len(skipped)} 个越界格（样本 {skipped[:3]}）",
            file=sys.stderr,
        )
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/jp-foodservice-trend/spider.py [limit]
    arg = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    print(json.dumps(run_jp_foodservice_trend(limit=arg), ensure_ascii=False, indent=2))
