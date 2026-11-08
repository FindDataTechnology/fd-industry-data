"""spiders/moa-variety-approval —— 农业农村部种业管理司「公示公告」（**事件面**）。

数据表面（2026-11-08 实测）：

- 列表 ``https://zys.moa.gov.cn/gsgg/``，静态 HTML，UTF-8，**19 条/页**；
  分页 ``index_N.htm``（``index_1``/``index_2`` 均 200，``index_99`` 404，
  翻页以 404 终止）。
- 条目形态：``<a href="./YYYYMM/tYYYYMMDD_<id>.htm">标题</a>`` 后紧跟发布日期。
- 三类公告混排：
  1. ``关于<省>审定通过的主要农作物品种名称等信息的公示``（省级审定，品种清单）
  2. ``关于第N届国家农作物品种审定委员会第M次审定会议初审通过…品种…的公示``（国家审定）
  3. ``<省>农业农村厅关于核发选育生产经营相结合、有效区域为全国的种子生产经营许可证的公示``
  4. ``…拟撤销审定…``（撤销审定）

**为什么是事件面（实测结论，不是偷懒）**：详情页 **`<table>` 数 = 0**，品种清单
（品种名/作物/审定编号）全部放在 **`.doc` 附件**里，且走政务 reader 代理
（``https://zys.moa.gov.cn/zxfile/reader?file=…/P0….doc``）。本单元：

- **不解析 .doc**（没有平台共享层，也不为它引入新依赖）；
- **不 OCR**（与 2026-10-13 裁决一致）；
- 因此**只登记事件，不产出任何数值观测行**——先例 ``spiders/samr-food-inspection/``。

清单本身要看请点开附件；本单元如实标注「清单见附件，未解析」，而不是伪装成有数值。

**附件存在性不猜**：默认不逐条抓详情页（19 条/页 × 多页对政务站请求量偏大）。
``with_detail=True`` 时才逐条进详情页数附件数；默认 ``attachment_count=None``、
``has_attachment=None``（**未知就记未知，不写 False**）。

``notice_type`` 分类与 ``province`` / ``scope`` 全部从**标题**解析，不依赖详情页。

只用标准库：``urllib`` + ``re`` + ``html``。
"""
from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

SOURCE = "moa-variety-approval"
BASE = "https://zys.moa.gov.cn/gsgg/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 45
#: 实测 index_99 = 404；作为硬上限，避免无限翻页
MAX_PAGES = 40

_ITEM_RE = re.compile(
    r"href=[\"']?(\./\d{6}/t\d{8}_\d+\.htm)[\"']?[^>]*>(.*?)</a>\s*([\d-]{8,10})?",
    re.S,
)
_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_ATTACH_RE = re.compile(
    r"(?:zxfile/reader\?file=[^\"'\s>]*\.(?:doc|docx|pdf|xls|xlsx)"
    r"|/[^\"'\s>]*\.(?:doc|docx|pdf|xls|xlsx))",
    re.I,
)
_PROVINCE_RE = re.compile(
    # 「关于四川省审定…」 / 「河北省农业农村厅关于…」 / 「内蒙古自治区农牧厅关于…」
    r"关于([\u4e00-\u9fa5]{2,12}?(?:省|自治区|市))"
)
_PROVINCE_LEAD_RE = re.compile(r"^([\u4e00-\u9fa5]{2,12}?(?:省|自治区|市))")
_TRAIL_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})\s*$")
_NATIONAL_HINT = "国家农作物品种审定委员会"

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# HTTP（国内直连，禁代理）
# --------------------------------------------------------------------------
def _build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(opener: urllib.request.OpenerDirector, url: str) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            status = getattr(resp, "status", 200)
            if status != 200:
                logger.info("%s: HTTP %s — stop", url, status)
                return None
            body = resp.read()
    except urllib.error.HTTPError as exc:
        logger.info("%s: HTTP %s — stop", url, exc.code)
        return None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        logger.warning("%s: fetch failed (%s) — skip page", url, exc)
        return None
    for enc in ("utf-8", "gb18030"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------
def classify(title: str) -> str:
    """标题 → notice_type（闭集）。"""
    if "拟撤销审定" in title or "撤销审定" in title:
        return "variety_revocation"
    if "种子生产经营许可证" in title:
        return "seed_license"
    if _NATIONAL_HINT in title:
        return "national_variety_approval"
    if "审定通过" in title and "品种名称" in title:
        return "provincial_variety_approval"
    return "other"


def extract_province(title: str) -> str | None:
    """标题 → 省级行政区名（如「四川省」「内蒙古自治区」）；国家层面公告返回 None。"""
    if _NATIONAL_HINT in title:
        return None
    m = _PROVINCE_RE.search(title) or _PROVINCE_LEAD_RE.search(title)
    return m.group(1).strip() if m else None


def _split_trailing_date(title: str) -> tuple[str, str]:
    """把锚点文本尾部的日期摘出来（实测日期在 ``</a>`` 里面）。"""
    m = _TRAIL_DATE_RE.search(title)
    if not m:
        return title, ""
    pub = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return title[: m.start()].strip(), pub


def _parse_list(page: str) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for m in _ITEM_RE.finditer(page):
        rel, inner, tail = m.group(1), m.group(2), m.group(3) or ""
        title = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", inner)).strip()
        if not title or rel in seen:
            continue
        # 发布日期实测粘在锚点文本尾部；先摘掉
        title, pub = _split_trailing_date(title)
        if not pub:
            dm = _DATE_RE.search(tail)
            if dm:
                pub = (f"{int(dm.group(1)):04d}-{int(dm.group(2)):02d}"
                       f"-{int(dm.group(3)):02d}")
        seen.add(rel)
        out.append({"rel": rel, "title": title, "publish_date": pub})
    return out


def _count_attachments(page: str) -> int:
    return len(set(_ATTACH_RE.findall(page)))


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def run_moa_variety_approval(
    limit: int = 40,
    window_start: str | None = None,
    window_end: str | None = None,
    with_detail: bool = False,
) -> list[dict]:
    """登记种业管理司公示公告事件（**事件面：不产数值观测**）。

    ``limit`` 计**事件条数**；``window_start``/``window_end``（YYYY-MM 或
    YYYY-MM-DD）按**发布日期**过滤，先过滤后截断。``with_detail=True`` 时逐条
    进详情页数附件（多一批请求，默认关闭）。
    """
    if (window_start is None) != (window_end is None):
        raise ValueError(
            f"moa-variety-approval: window_start/window_end 须成对出现，"
            f"got start={window_start!r} end={window_end!r}"
        )

    def _norm(v: str) -> str:
        v = v.strip()
        return v[:10] if len(v) >= 10 else v

    lo = _norm(window_start) if window_start else None
    hi = _norm(window_end) if window_end else None
    if lo and hi and lo > hi:
        raise ValueError(
            f"moa-variety-approval: window_start {lo} 晚于 window_end {hi}"
        )

    opener = _build_opener()
    scraped_at = datetime.now(timezone.utc).isoformat()
    events: list[dict] = []
    seen: set[str] = set()
    pages = 0

    for idx in range(1, MAX_PAGES + 1):
        url = BASE if idx == 1 else f"{BASE}index_{idx}.htm"
        page = _get(opener, url)
        if page is None:
            break
        pages += 1
        items = _parse_list(page)
        if not items:
            break
        stop = False
        for item in items:
            if item["rel"] in seen:
                continue
            seen.add(item["rel"])
            pub = item["publish_date"]
            if lo and (not pub or pub < lo):
                continue
            if hi and (not pub or pub > hi):
                continue
            events.append(item)
            if len(events) >= limit:
                stop = True
                break
        if stop:
            break
        # 已翻到窗口下界之前 → 之后只会更旧，停止
        if lo and items and all(
            (it["publish_date"] or "") and it["publish_date"] < lo for it in items
        ):
            break

    if not events:
        raise RuntimeError(
            f"moa-variety-approval: 0 events parsed from {pages} page(s) of {BASE}"
        )

    rows: list[dict] = []
    for ev in events:
        article_url = "https://zys.moa.gov.cn/gsgg/" + ev["rel"].lstrip("./")
        attachment_count = None
        has_attachment = None
        if with_detail:
            detail = _get(opener, article_url)
            if detail is not None:
                attachment_count = _count_attachments(detail)
                has_attachment = attachment_count > 0
        title = ev["title"]
        rows.append(
            {
                "publish_date": ev["publish_date"],
                "title": title,
                "notice_type": classify(title),
                "province": extract_province(title),
                "scope": "national" if _NATIONAL_HINT in title else "provincial",
                "url": article_url,
                "has_attachment": has_attachment,
                "attachment_count": attachment_count,
                "variety_list_in_attachment": (
                    None if has_attachment is None else has_attachment
                ),
                "scraped_at": scraped_at,
            }
        )

    logger.info(
        "moa-variety-approval: %d event(s) from %d page(s); detail=%s",
        len(rows), pages, with_detail,
    )
    return rows[: max(int(limit), 0)] if limit is not None else rows


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for row in run_moa_variety_approval(limit=5):
        print(row)
