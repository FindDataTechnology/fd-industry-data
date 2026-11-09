"""新世纪评级（上海新世纪资信评估投资服务有限公司）评级公告 —— 银行跟踪评级。

站点实读（2026-11-08）:

- 官网 ``http://www.shxsj.com/`` **只有 HTTP**：``https://www.shxsj.com:443``
  连接被拒（curl exit 7），``http`` 返回 200。页面是 Vue SPA，数据全部走
  ``http://www.shxsj.com/serve/api/`` 下的 POST 接口（form-urlencoded）。
- 列表接口 ``article_api/get_cid_article``，参数::

      cid=50            评级公告（栏目根，子栏目 51 首次评级 / 61 跟踪评级 /
                        207 主动评级 / 71 其他信息）
      keyword=银行      服务端关键字过滤（实测 total 29993 → 1081）
      rank_type=跟踪评级 服务端评级类型过滤（cid=50 & keyword=银行 实测 1081 → 824）
      page=N            1 起，固定 per_page=15（传 per_page 无效）

  响应 ``{"code":200,"data":{"list":{"total":…,"per_page":15,
  "current_page":N,"last_page":…,"data":[…]}}}``。
- 每条记录::

      {id, cid, cate_name, add_time, title(空), pdf:[{name, url}], nav:[{id,title}],
       project:{year, rank_type, main_type, rank_level, bound_type, rank_time,
                issuer, add_time, …}}

  ``project.main_type`` = 主体等级，``bound_type`` = 债项等级，
  ``rank_level`` = 评级展望，``rank_time`` = 评级日期，``issuer`` = 债券全称
  （银行名在其前缀里），``pdf[].url`` 形如
  ``/uploadfile/genzongen/2026/<文件名>.pdf``（相对路径，需拼 ``BASE``）。
- 附件 PDF 首版文本有**字间空格**（poppler 提取结果）::

      贵 阳农村商业 银行 股份有 限公 司   →   贵阳农村商业银行股份有限公司

  清理规则见 ``_clean_pdf_text``：CJK↔CJK、CJK↔中文标点之间的空白全部删除，
  其余连续空白折叠为一个空格（保留西文/数字间的词边界）。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from fd_industry_data import pdftext

BASE = "http://www.shxsj.com"
LIST_URL = BASE + "/serve/api/article_api/get_cid_article"
REFERER = BASE + "/page"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": REFERER,
    "Content-Type": "application/x-www-form-urlencoded",
}

SLEEP = 1.2          # 网络礼节：连续请求间隔 ≥ 1s
PER_PAGE = 15        # 上游固定 15 条/页（per_page 参数实测无效）
RETRIES = 3

_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_CJK_PUNCT = r"，。、；：？！“”‘’（）《》【】〈〉—…·「」『』"
_SPACE_CJK_CJK = re.compile(rf"(?<=[{_CJK}])[ \t\u00a0]+(?=[{_CJK}])")
_SPACE_CJK_PUNCT = re.compile(
    rf"(?<=[{_CJK}])[ \t\u00a0]+(?=[{_CJK_PUNCT}])|(?<=[{_CJK_PUNCT}])[ \t\u00a0]+(?=[{_CJK}])"
)
_SPACE_RUN = re.compile(r"[ \t\u00a0]+")
_YEAR_PREFIX = re.compile(r"^\d{4}年")
_BANK_TAIL = re.compile(r"^(.{2,40}?(?:银行|信用社|农村商业银行|农商行))")
_COMPANY = re.compile(r"(?:股份有限公司|有限责任公司|银行|信用社)")
_PERIOD_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _post(payload: dict[str, str], *, timeout: int = 40) -> dict:
    """POST 列表接口，返回解析后的 JSON；3 次重试后抛 ``URLError``。"""
    body = urllib.parse.urlencode(payload).encode()
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(LIST_URL, data=body, headers=HEADERS)
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            doc = json.loads(raw.decode("utf-8", "replace"))
            if doc.get("code") != 200:
                raise ValueError(f"接口 code={doc.get('code')!r} msg={doc.get('msg')!r}")
            return doc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last = exc
            if attempt + 1 < RETRIES:
                time.sleep(1.0 + attempt)
    raise urllib.error.URLError(f"get_cid_article 连续 {RETRIES} 次失败：{last}")


def _fetch_page(*, cid: int, page: int, keyword: str, rank_type: str) -> dict:
    """取一页；返回 ``data.list``（含 total/per_page/last_page/data）。"""
    payload: dict[str, str] = {"cid": str(cid), "page": str(page)}
    if keyword:
        payload["keyword"] = keyword
    if rank_type:
        payload["rank_type"] = rank_type
    doc = _post(payload)
    listing = (doc.get("data") or {}).get("list") or {}
    if not isinstance(listing, dict):
        raise ValueError(f"data.list 形态异常：{type(listing).__name__}")
    return listing


def _clean_pdf_text(text: str) -> str:
    """清掉 PDF 文本的字间空格，其它空白折叠为单空格。

    实测样例行（``pdftotext -layout`` 首页）::

        贵 阳农村商业 银行 股份有 限公 司
        → 贵阳农村商业银行股份有限公司

        "项 目负 责 人    宫晨"  →  "项目负责人 宫晨"（西文/数字间的边界保留一个空格）
    """
    lines: list[str] = []
    for line in text.splitlines():
        line = line.replace("\u3000", " ")
        line = _SPACE_CJK_PUNCT.sub("", line)
        line = _SPACE_CJK_CJK.sub("", line)
        line = _SPACE_RUN.sub(" ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _pdf_text(payload: bytes) -> str:
    """PDF 字节 → 清洗后的首页文本；pdftotext 缺失或失败时返回空串。"""
    if not pdftext.available() or not pdftext.is_pdf(payload):
        return ""
    try:
        return _clean_pdf_text(pdftext.extract_text(payload, layout=True))
    except (pdftext.PdfTextError, OSError, subprocess.SubprocessError):
        return ""


def _pdf_subject(text: str) -> str:
    """从首页文本取发行人主体名（首条含 银行/信用社/公司 的短行）。"""
    for line in text.splitlines():
        line = line.strip()
        if 4 <= len(line) <= 60 and _COMPANY.search(line):
            return line
    return ""


def _bank_name(issuer: str) -> str:
    """从债券全称的前缀剥离银行名：``2023年浙江义乌农村商业银行…`` → ``浙江义乌农村商业银行``。"""
    text = _YEAR_PREFIX.sub("", (issuer or "").strip())
    matched = _BANK_TAIL.match(text)
    return matched.group(1) if matched else text[:40]


def _period_of(record: dict) -> str:
    project = record.get("project") or {}
    for key in ("rank_time", "add_time"):
        stamp = str(project.get(key) or record.get(key) or "").strip()
        if len(stamp) >= 7:
            return stamp[:7]
    return ""


def _row(record: dict, stamp: str) -> dict | None:
    project = record.get("project") or {}
    pdfs = record.get("pdf") or []
    pdf = pdfs[0] if pdfs and isinstance(pdfs[0], dict) else {}
    issuer = str(project.get("issuer") or "").strip()
    if not issuer and not pdf:
        return None
    rel = str(pdf.get("url") or "").strip()
    nav = record.get("nav") or []
    row = {
        "doc_id": int(record.get("id") or 0),
        "cid": str(record.get("cid") or ""),
        "category": str(record.get("cate_name") or "").strip(),
        "nav_path": "/".join(
            str(node.get("title") or "").strip() for node in nav if isinstance(node, dict)
        ),
        "rank_type": str(project.get("rank_type") or "").strip(),
        "bank_name": _bank_name(issuer),
        "issuer": issuer,
        "issuer_rating": str(project.get("main_type") or "").strip(),
        "bond_rating": str(project.get("bound_type") or "").strip(),
        "outlook": str(project.get("rank_level") or "").strip(),
        "rating_date": str(project.get("rank_time") or "").strip(),
        "published_at": str(record.get("add_time") or project.get("add_time") or "").strip(),
        "pdf_name": str(pdf.get("name") or "").strip(),
        "pdf_url": BASE + rel if rel.startswith("/") else rel,
        "pdf_subject": "",
        "source": "xsj-ratings",
        "scraped_at": stamp,
    }
    return row


def _enrich_pdf(row: dict) -> None:
    rel = row["pdf_url"]
    if not rel:
        return
    try:
        req = urllib.request.Request(rel, headers={k: HEADERS[k] for k in ("User-Agent", "Referer")})
        payload = urllib.request.urlopen(req, timeout=90).read()
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"[xsj-ratings] {row['doc_id']} PDF 下载失败：{exc}", flush=True)
        return
    row["pdf_subject"] = _pdf_subject(_pdf_text(payload))


def run_xsj_ratings(
    limit: int = 200,
    keyword: str = "银行",
    pages: int = 3,
    rank_type: str = "跟踪评级",
    cid: int = 50,
    page: int = 1,
    period: str = "",
    pdf_text: bool = False,
) -> list[dict]:
    """新世纪评级「银行跟踪评级」条目（一条评级公告一行，含 PDF 附件直链）。

    Args:
        limit: 返回行数上限。
        keyword: 服务端关键字过滤（默认 ``银行``；空串 = 不过滤）。
        pages: 从 ``page`` 起连续取多少页（上游固定 15 条/页）。
        rank_type: 服务端评级类型过滤（默认 ``跟踪评级``；空串 = 不限）。
        cid: 栏目 id（默认 ``50`` 评级公告根栏目）。
        page: 起始页（1 起），用于把 golden 钉在固定页。
        period: ``YYYY-MM``，按评级日期（缺省用发布日期）过滤；空 = 不过滤。
        pdf_text: 为 True 时逐条下载 PDF，用 pdftotext 提取首页并填 ``pdf_subject``
            （字间空格已清理）；默认关闭以免每次跑几十个 5MB 附件。

    Raises:
        RuntimeError: 所有页请求失败，或过滤后没有任何条目。
        ValueError: ``period`` 不是 ``YYYY-MM``。
    """
    if limit <= 0 or pages <= 0:
        return []
    if period and not _PERIOD_RE.match(period.strip()):
        raise ValueError(f"period 须为 YYYY-MM，收到 {period!r}")
    month = period.strip()
    stamp = _now()

    rows: list[dict] = []
    seen: set[int] = set()
    failures = 0
    last_page = None
    for offset in range(int(pages)):
        current = int(page) + offset
        if last_page is not None and current > last_page:
            break
        try:
            listing = _fetch_page(cid=cid, page=current, keyword=keyword, rank_type=rank_type)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            failures += 1
            print(f"[xsj-ratings] 第 {current} 页失败：{exc}", flush=True)
            continue
        last_page = int(listing.get("last_page") or 0)
        records = listing.get("data") or []
        kept = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            if month and _period_of(record) != month:
                continue
            row = _row(record, stamp)
            if row is None or row["doc_id"] in seen:
                continue
            seen.add(row["doc_id"])
            rows.append(row)
            kept += 1
        print(
            f"[xsj-ratings] page {current}/{last_page} 取 {len(records)} 条，"
            f"过滤后 {kept} 条（total={listing.get('total')}）",
            flush=True,
        )
        if current >= last_page:
            break
        time.sleep(SLEEP)

    if failures and len(rows) == 0 and failures >= int(pages):
        raise RuntimeError("xsj-ratings: 所有页请求均失败")
    if not rows:
        raise RuntimeError(
            f"xsj-ratings: 无匹配条目（cid={cid} keyword={keyword!r} "
            f"rank_type={rank_type!r} period={period!r}）"
        )
    rows = rows[: int(limit)]
    if pdf_text:
        for row in rows:
            _enrich_pdf(row)
            time.sleep(SLEEP)
    return rows
