"""spiders/education_cc_courses —— CC 课程层：OCW sitemap 编目 + Open edX 公开实例目录。

2026-11-04 实测管道定稿（覆盖 1.5 的手写样本，样本仅作结构参考）：

- **MIT OCW**：`https://ocw.mit.edu/sitemap.xml` 是 sitemapindex，**每课一条**
  `<loc>https://ocw.mit.edu/courses/<number>-<title>-<term>-<year>/sitemap.xml</loc>`
  ——父目录即课程页，slug 直接解出课程号/标题/学期。全站许可 CC BY-NC-SA
  （站点 terms-of-use 声明，1.2 复核）→ 每行挂 commercial_ok=False。
  正文层（ocw-data-parser markdown）是集群作业，本 spider 交付编目+外链（link_only），
  不伪装成已落正文。
- **Open edX 公开实例**：`GET <instance>/api/courses/v1/courses/?page_size=100&page=N`
  → results 为 list（2026-11-04 实测 OL Library 结构：item keys 含
  course_id/name/org/short_description/pacing/effort/media——标题字段是 name 非 title）。
  视频多挂 YouTube/站内流，编目行 body_state=link_only。默认实例：
  openlearninglibrary.mit.edu（实测 200）。
- **OER WorldMap**：⚠️ 2026-11-04 复测 `/get/`、`/api/` 均返回 SPA HTML——公开 JSON-LD
  API 已退役（1.5 当时依据的调研结论过期）。**降人工录入通道**（design D8 的
  curated 道），supply-manifest 已同步；本 spider 不实现自动适配器，不静默替代。

spec 落点：cap1 R1（manifest 接入）、R2（CC BY-NC 落库但限用+禁商用标记）、
R4（四字段溯源：source_url/source/scraped_at/license 全行必带）。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

SOURCE = "education-cc-courses"
OCW_SITEMAP = "https://ocw.mit.edu/sitemap.xml"
OCW_COURSE_RE = re.compile(r"/courses/([0-9][^/]*?)/sitemap\.xml$")
TIMEOUT = 60


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "fd-industry-data/1.0 (education-cc-courses)"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def classify_license(license: str | None) -> tuple[str, bool]:
    """许可归层（design D3 枚举）→ (layer, commercial_ok)。

    纯函数——spec cap1 R2「用途闸的判定依据」交付件（cap2 R2 消费，M3 装配时拦截）。
    """
    s = (license or "").strip().lower().replace("_", "-")
    s = re.sub(r"\s+", "-", s)  # "CC BY 4.0" / "CC BY-NC-SA" 空格统一成连字符
    if not s:
        return "metadata_only", True
    if s in ("cc0", "public-domain", "pd"):
        return "cc0_pd", True
    if "by-nc" in s:
        return "cc-by-nc", False
    if "by-sa" in s and "nc" not in s:
        return "oa_cc_by", True
    if s.startswith("cc-by") or s in ("oa", "cc"):
        return "oa_cc_by", True
    if s in ("metadata-only",):
        return "metadata_only", True
    return "metadata_only", True  # 未识别一律降级（宁限不放，spec cap1 R2）


def run_ocw_catalog(limit: int = 500) -> list[dict]:
    """OCW 编目：sitemapindex 解析 → 逐课程行（link_only + 禁商用标记）。"""
    xml = _fetch(OCW_SITEMAP)
    root = ET.fromstring(xml)
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for loc in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
        url = (loc.text or "").strip()
        if not url.startswith("https://ocw.mit.edu/courses/"):
            continue
        m = OCW_COURSE_RE.search(url)
        if not m:
            continue
        slug = m.group(1)
        # slug 形如 6-0001-introduction-to-computer-science-and-programming-in-python-fall-2016
        term_year = re.match(r"^(.*)-(fall|spring|summer|winter|iwm|swe)-(\d{4})$", slug)
        course_page = f"https://ocw.mit.edu/courses/{slug}/"
        rows.append({
            "resource_id": f"edu.course.ocw.{slug}",
            "course_slug": slug,
            "course_number": slug.split("-")[0] if re.match(r"^\d", slug) else None,
            "title": term_year.group(1) if term_year else slug,
            "term": f"{term_year.group(2)} {term_year.group(3)}" if term_year else None,
            "org": "MIT OpenCourseWare",
            "platform": "MIT OCW",
            "license": "CC BY-NC-SA",
            "license_layer": "cc-by-nc",
            "commercial_ok": False,  # spec cap1 R2 禁商用标记
            "body_state": "link_only",  # 正文层=集群 parser 作业，不伪装已落
            "education_source": "ocw_sitemap",
            "source": SOURCE,
            "source_url": course_page,
            "scraped_at": now,
        })
        if len(rows) >= limit:
            break
    if not rows:
        raise RuntimeError("OCW sitemap returned no course entries — upstream frozen or surface changed")
    return rows


DEFAULT_OPENEDX_INSTANCE = "https://openlearninglibrary.mit.edu"


def run_openedx_catalog(instance: str = DEFAULT_OPENEDX_INSTANCE, limit: int = 200) -> list[dict]:
    """Open edX 公开实例目录分页拉取（title 字段=name，2026-11-04 实测）。

    分页越界坑（2026-11-04 首批真落库撞出）：OL Library 翻过最后一页后
    某实例会返回 HTTP 404 而非空 results——首页成功后任何页 404=目录尽头，
    优雅停；连第一页都失败才显式报错（上游变更可见）。
    """
    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    page = 1
    first_page_ok = False
    while len(rows) < limit:
        url = f"{instance.rstrip('/')}/api/courses/v1/courses/?page_size=100&page={page}"
        try:
            payload = json.loads(_fetch(url))
        except urllib.error.HTTPError as e:
            if first_page_ok:
                break  # 目录尽头（越界 404）——正常结束
            raise RuntimeError(f"Open edX instance {instance} first page failed: {e}") from e
        results = payload.get("results") or []
        if not results:
            break
        first_page_ok = True
        for c in results:
            cid = c.get("id") or c.get("course_id")
            rows.append({
                "resource_id": f"edu.course.openedx.{cid}",
                "course_id": cid,
                "title": c.get("name") or "",
                "org": c.get("org"),
                "platform": urllib.parse.urlparse(instance).netloc,
                "short_description": c.get("short_description"),
                "pacing": c.get("pacing"),
                "effort": c.get("effort"),
                "start": c.get("start"),
                "end": c.get("end"),
                # 卡层通道的许可状态本身就是声明值「metadata-only」，不是空缺——
                # 2026-11-04 试点暴露：license=None 被 publish_gate 整通道拦在发布态外
                "license": "metadata-only",
                "license_layer": classify_license(None)[0],
                "commercial_ok": classify_license(None)[1],
                "body_state": "link_only",  # 视频挂 YouTube/站内流，不取正文
                "education_source": "openedx_catalog",
                "source": SOURCE,
                "source_url": f"{instance.rstrip('/')}/courses/{cid}",
                "scraped_at": now,
            })
            if len(rows) >= limit:
                break
        page += 1
    if not rows:
        raise RuntimeError(f"Open edX instance {instance} returned no courses — surface changed")
    return rows


def run_education_cc_courses(limit: int = 500) -> list[dict]:
    """runner_cli 约定入口（slug 连字符→下划线）：本源主入口 = MIT OCW sitemap 编目。

    runner_cli._load_entry 恒定只解析 ``run_<slug>``；本单元有多个具名命令
    （run_ocw_catalog / run_openedx_catalog）且都由 provider.py 按名分派，但
    巡检/调度只认这一个入口，故在此固定主入口并透传 ``limit``——缺此包装时
    多 run_* 被判歧义 → exit 4，且失败发生在 open_run_for 报到前，30 天巡检
    看不见（education 三源基线盲区）。Open edX 目录仍经 provider 单独调用。
    """
    return run_ocw_catalog(limit=limit)


if __name__ == "__main__":
    rows = run_ocw_catalog(limit=3)
    for r in rows:
        print(f"OCW {r['course_number']} | {r['title'][:50]} | nc={not r['commercial_ok']}")
    rows = run_openedx_catalog(limit=3)
    for r in rows:
        print(f"edX {r['course_id'][:45]} | {r['title'][:40]}")
