"""spiders/education-openalex —— OpenAlex 教育子域全量/增量入库。

数据表面：
- 快照：openalex.s3.amazonaws.com/data/jsonl/works/updated_date=YYYY-MM-DD/part_*.gz（JSONL.gz）
  季度全量 + deleted_ids.csv.gz + merged_ids.csv.gz（付费档）。本 spider 免费档：仅消费 updated_date 分区增量。
- API：api.openalex.org/works（免费，礼貌 mailto，~10 req/s；区间/主题过滤）

核心口径（Wave 0 1.3 实测定稿）：
- Education 子域 = subfield 3304 + 三跨域补全 topic（T13106/T11875/T12171）
- 元数据骨架：~7.6M 行，压缩 Parquet ~10.4 GB
- OA 正文子集：~3.1M 行（is_oa:true），压缩 ~4.3 GB + PDF 下载另走付费通道

增量策略：
- 每月跑一次：消费 updated_date 分区（上月分区）+ deleted_ids/merged_ids（如付费档可得）
- 免费档：仅 updated_date 增量；deleted/merged 无付费档时靠 API 再核对（抽样）

Phase 语义：
- skeleton：仅元数据骨架（license=cc0, body_state=link_only）
- oa_fulltext：OA 正文层（抓取 PDF/TEI URL，标记 body_state=stored_extraction，另起下载任务）
- incremental：updated_date 分区同步（默认每月跑）

参考：spiders/openalex-works/spider.py（月度计数）、spiders/edgar-emissions/spider.py（流式下载+解析）
"""
from __future__ import annotations

import gzip
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# ──────────────────────────────────────────────────────────────────────────────
# 常量与配置
# ──────────────────────────────────────────────────────────────────────────────

SOURCE = "education-openalex"
SNAPSHOT_BASE = "https://openalex.s3.amazonaws.com/data/jsonl/works"
API_URL = "https://api.openalex.org/works"
MAILTO = "research@finddata.example"  # polite pool

HEADERS = {
    "User-Agent": f"fd-industry-data/1.0 (mailto:{MAILTO})",
    "Accept": "application/json",
}
TIMEOUT = 300  # 秒；下载 gz 分区留足
REQUEST_GAP = 0.12  # 秒；~8 req/s < 10 req/s 上限

# Education 子域口径（Wave 0 1.3 实测定稿）
EDUCATION_SUBFIELD = "https://openalex.org/subfields/3304"
CROSS_DOMAIN_TOPICS = [
    "https://openalex.org/T13106",  # Neuroscience, Education and Cognitive Function
    "https://openalex.org/T11875",  # Statistics Education and Methodologies
    "https://openalex.org/T12171",  # Open Education and E-Learning
]

# OpenAlex topic -> education_source 标签映射
TOPIC_SOURCE_MAP = {
    EDUCATION_SUBFIELD: "subfield_3304",
    "https://openalex.org/T13106": "topic_T13106",
    "https://openalex.org/T11875": "topic_T11875",
    "https://openalex.org/T12171": "topic_T12171",
}

# 快照分区正则
PARTITION_RE = re.compile(r"updated_date=(\d{4}-\d{2}-\d{2})/part_\d+\.gz$")

# 本地缓存目录（可被环境变量覆盖）
CACHE_DIR = Path(os.getenv("EDUCATION_OPENALEX_CACHE", "/tmp/education-openalex-cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────────────────────

def _get_json(url: str) -> dict:
    """GET 一个 JSON 端点（仅固定 https 主机；无鉴权、无重试放大）。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _stream_gz_lines(url: str) -> Iterator[dict]:
    """流式下载 gz 并逐行 yield JSON（内存不随行数膨胀）。"""
    req = urllib.request.Request(url, headers={**HEADERS, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        # resp 已自动解压 gzip（urllib 默认处理 Content-Encoding: gzip）
        # 但 S3 返回的 .gz 是原始 gzip 流，urllib 可能不自动解压
        # 这里显式用 gzip.GzipFile 处理
        import io
        gz = gzip.GzipFile(fileobj=io.BytesIO(resp.read()))
        for line in gz:
            line = line.strip()
            if line:
                yield json.loads(line.decode("utf-8", "replace"))


def _download_gz_to_cache(url: str, cache_path: Path) -> Path:
    """下载 gz 到本地缓存（断点续传简易版：文件存在且大小>0 则复用）。"""
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return cache_path
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp, open(cache_path, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)
    return cache_path


def _iter_partition_gz(url: str) -> Iterator[dict]:
    """流式迭代一个快照分区的 JSONL.gz（优先本地缓存）。"""
    cache_path = CACHE_DIR / Path(url).name
    _download_gz_to_cache(url, cache_path)
    with gzip.open(cache_path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _build_api_filter(education_source: str) -> str:
    """构造 API filter 字符串。"""
    if education_source == "subfield_3304":
        return f"primary_topic.subfield.id:{EDUCATION_SUBFIELD}"
    elif education_source == "topic_T13106":
        return f"primary_topic.id:https://openalex.org/T13106"
    elif education_source == "topic_T11875":
        return f"primary_topic.id:https://openalex.org/T11875"
    elif education_source == "topic_T12171":
        return f"primary_topic.id:https://openalex.org/T12171"
    raise ValueError(f"Unknown education_source: {education_source}")


def _fetch_api_page(filter_str: str, cursor: str | None = None, per_page: int = 200) -> dict:
    """抓取 API 单页（带 cursor 分页）。"""
    params = {
        "filter": filter_str,
        "per-page": per_page,
        "mailto": MAILTO,
    }
    if cursor:
        params["cursor"] = cursor
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    time.sleep(REQUEST_GAP)
    return _get_json(url)


def _extract_work_row(work: dict, education_source: str) -> dict:
    """从 OpenAlex work 对象抽取目录行（扁平化，JSON 字段序列化为字符串）。"""
    # 基础字段
    row = {
        "work_id": work.get("id"),
        "doi": work.get("doi"),
        "title": work.get("title"),
        "display_name": work.get("display_name"),
        "publication_year": work.get("publication_year"),
        "publication_date": work.get("publication_date"),
        "language": work.get("language"),
        "primary_location_oa": work.get("primary_location", {}).get("is_oa"),
        "primary_location_pdf_url": work.get("primary_location", {}).get("pdf_url"),
        "primary_location_license": work.get("primary_location", {}).get("license"),
        "open_access_is_oa": work.get("open_access", {}).get("is_oa"),
        "open_access_oa_status": work.get("open_access", {}).get("oa_status"),
        "open_access_oa_url": work.get("open_access", {}).get("oa_url"),
        "cited_by_count": work.get("cited_by_count"),
        "updated_date": work.get("updated_date"),
        "created_date": work.get("created_date"),
        "education_source": education_source,
        "source": "education-openalex",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }

    # 主题层级
    pt = work.get("primary_topic") or {}
    row["primary_topic_id"] = pt.get("id")
    row["primary_topic_display_name"] = pt.get("display_name")
    row["primary_subfield_id"] = (pt.get("subfield") or {}).get("id")
    row["primary_subfield_display_name"] = (pt.get("subfield") or {}).get("display_name")
    row["primary_field_id"] = ((pt.get("subfield") or {}).get("field") or {}).get("id")
    row["primary_field_display_name"] = ((pt.get("subfield") or {}).get("field") or {}).get("display_name")
    row["primary_domain_id"] = (((pt.get("subfield") or {}).get("field") or {}).get("domain") or {}).get("id")
    row["primary_domain_display_name"] = (((pt.get("subfield") or {}).get("field") or {}).get("domain") or {}).get("display_name")

    # Open Access 字段
    oa = work.get("open_access") or {}
    row["open_access_is_oa"] = oa.get("is_oa")
    row["open_access_oa_status"] = oa.get("oa_status")
    row["open_access_oa_url"] = oa.get("oa_url")

    # 主位置 license
    pl = work.get("primary_location") or {}
    row["primary_location_license"] = pl.get("license")
    row["primary_location_pdf_url"] = pl.get("pdf_url")
    row["primary_location_oa"] = pl.get("is_oa")

    # 溯源 URL（spec cap1 R4 四字段之一）：landing page → DOI → OpenAlex 页面 三级回落
    row["source_url"] = (
        pl.get("landing_page_url")
        or work.get("doi")
        or work.get("id")
    )
    # 增量机制字段（deleted_check/merged 对账消费）
    row["openalex_id"] = work.get("id")

    # 来源期刊/会议
    src = work.get("primary_location", {}).get("source") or {}
    row["source_id"] = src.get("id")
    row["source_display_name"] = src.get("display_name")
    row["source_issn_l"] = src.get("issn_l")
    row["source_is_oa"] = src.get("is_oa")
    row["source_is_in_doaj"] = src.get("is_in_doaj")

    # 引用计数
    row["cited_by_count"] = work.get("cited_by_count")

    # 复杂字段 → JSON 字符串
    def _json_dumps(v):
        return json.dumps(v, ensure_ascii=False) if v else None

    row["authors"] = _json_dumps(work.get("authorships"))
    # 收集机构（去重：按 id 去重）
    inst_seen = set()
    inst_list = []
    for auth in (work.get("authorships") or []):
        for inst in (auth.get("institutions") or []):
            inst_id = inst.get("id")
            if inst_id and inst_id not in inst_seen:
                inst_seen.add(inst_id)
                inst_list.append(inst)
    row["institutions"] = _json_dumps(inst_list)
    row["concepts"] = _json_dumps(work.get("concepts"))
    row["keywords"] = _json_dumps(work.get("keywords"))

    # license 与 body_state
    row["license"] = (
        work.get("open_access", {}).get("license")
        or work.get("primary_location", {}).get("license")
        or work.get("license")
    )
    row["body_state"] = "stored_extraction" if (
        work.get("open_access", {}).get("is_oa") and work.get("open_access", {}).get("oa_url")
    ) else "link_only"

    return row


# ──────────────────────────────────────────────────────────────────────────────
# Phase 实现
# ──────────────────────────────────────────────────────────────────────────────

def _run_skeleton(limit: int | None, education_source: str) -> list[dict]:
    """Phase: skeleton —— 走快照分区流式过滤（仅元数据骨架，license=cc0, body_state=link_only）。
    
    策略：遍历 S3 目录下的 updated_date 分区，流式读取 gz，按 education_source 过滤。
    由于 S3 目录无法直接列出所有分区，这里采用 API 游标分页方式（免费、稳定）。
    """
    filter_str = _build_api_filter(education_source)
    rows = []
    cursor = None
    fetched = 0
    max_pages = (limit // 200 + 2) if limit else None

    while True:
        if max_pages is not None and fetched >= limit:
            break
        page = _fetch_api_page(filter_str, cursor, per_page=200)
        meta = page.get("meta") or {}
        results = page.get("results") or []
        for work in results:
            row = _extract_work_row(work, education_source)
            row["license"] = "cc0"  # OpenAlex 元数据 CC0
            row["body_state"] = "link_only"
            rows.append(row)
            fetched += 1
            if limit and fetched >= limit:
                break
        cursor = meta.get("next_cursor")
        if not cursor or not results:
            break
    return rows


def _run_oa_fulltext(limit: int | None, education_source: str) -> list[dict]:
    """Phase: oa_fulltext —— OA 正文层（在 skeleton 基础上标记 body_state=stored_extraction，
    另起下载任务抓 PDF/TEI）。这里只做元数据标记，实际下载由单独任务处理。
    """
    filter_str = _build_api_filter(education_source) + ",open_access.is_oa:true"
    rows = []
    cursor = None
    fetched = 0
    max_pages = (limit // 200 + 2) if limit else None

    while True:
        if max_pages is not None and fetched >= limit:
            break
        page = _fetch_api_page(filter_str, cursor, per_page=200)
        results = page.get("results") or []
        for work in results:
            if not work.get("open_access", {}).get("is_oa"):
                continue
            row = _extract_work_row(work, education_source)
            row["license"] = work.get("open_access", {}).get("license") or "cc0"
            row["body_state"] = "stored_extraction" if work.get("open_access", {}).get("oa_url") else "link_only"
            rows.append(row)
            fetched += 1
            if limit and fetched >= limit:
                break
        cursor = page.get("meta", {}).get("next_cursor")
        if not cursor or not results:
            break
    return rows


def _list_s3_keys(prefix: str, max_keys: int = 1000) -> list[str]:
    """列 S3 桶某前缀下的全部 key（公开 list API，XML 分页）。"""
    import xml.etree.ElementTree as ET

    keys: list[str] = []
    token: str | None = None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": str(max_keys)}
        if token:
            params["continuation-token"] = token
        url = f"https://openalex.s3.amazonaws.com/?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as resp:
            root = ET.fromstring(resp.read())
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        for node in root.findall(".//s3:Contents/s3:Key", ns):
            keys.append(node.text or "")
        trunc = root.find("s3:IsTruncated", ns)
        token_node = root.find("s3:NextContinuationToken", ns)
        if trunc is not None and (trunc.text or "").lower() == "true" and token_node is not None:
            token = token_node.text
        else:
            break
    return keys


def _is_education_work(work: dict, education_source: str) -> bool:
    """快照行教育过滤（口径与 API count 一致：primary_topic 层判定）。"""
    pt = work.get("primary_topic") or {}
    if education_source == "subfield_3304":
        return (pt.get("subfield") or {}).get("id") == EDUCATION_SUBFIELD
    # 跨域补全 topic：按 topic id 匹配
    topic_id = pt.get("id")
    if education_source == "topic_T13106":
        return topic_id == "https://openalex.org/T13106"
    if education_source == "topic_T11875":
        return topic_id == "https://openalex.org/T11875"
    if education_source == "topic_T12171":
        return topic_id == "https://openalex.org/T12171"
    return False


def _run_incremental(limit: int | None, education_source: str,
                     date_prefix: str | None = None) -> list[dict]:
    """Phase: incremental —— S3 updated_date 分区直读（**显式指定日期**，作战式补数）。

    ⚠️ 体量事实（2026-11-04 实测）：works 日分区 = 25 片 × ~583MB ≈ **14.6 GB/天**
    （全天触碰件洪流，月度 ≈450GB）。因此本 phase **不做常规月度增量**：
    - 常规骨架 = 季度全量快照下载+本地过滤（集群作业，~750GB/季）
    - 常规增量 = deleted_ids.csv.gz（234MB 公开）对账撤回 + API 单件刷新
      （见 ``_deleted_ids``）
    本 phase 仅在显式传 ``date_prefix``（如 "2026-10-01"）时流式读该日分区——
    用于缺口修复；未传则显式报错，绝不静默下载整月。
    """
    if not date_prefix:
        raise RuntimeError(
            "incremental: S3 works 日分区≈14.6GB/天（洪流），禁止无 date_prefix 的"
            "常规月度跑批。常规增量走 deleted_ids 对账（phase=deleted_check）+ "
            "API 单件刷新；骨架走季度全量快照（集群作业）。缺口修复请显式传"
            " date_prefix='YYYY-MM-DD'。"
        )

    prefix = f"data/jsonl/works/updated_date={date_prefix}"
    keys = [k for k in _list_s3_keys(prefix) if PARTITION_RE.search(k)]
    if not keys:
        raise RuntimeError(
            f"incremental: no snapshot partitions under {prefix} "
            f"(bad date or upstream frozen) — refusing to return empty"
        )

    rows: list[dict] = []
    for key in sorted(keys):
        url = f"https://openalex.s3.amazonaws.com/{urllib.parse.quote(key)}"
        for work in _iter_partition_gz(url):
            if not _is_education_work(work, education_source):
                continue
            row = _extract_work_row(work, education_source)
            row["license"] = row.get("license") or "cc0"
            rows.append(row)
            if limit and len(rows) >= limit:
                return rows
    return rows


def _deleted_ids(limit: int | None) -> list[dict]:
    """流式读 deleted_ids.csv.gz（公开，234MB/季），返回已撤回 work ID 行。

    撤回传导口径：本函数只产出 (work_id, withdrawn) 行；与库内存量 work_id 的
    交集判定由 ingest 层完成（蜘蛛不连库）。CROSSREF：spec cap1 R5「上游修订与
    删除传导」的蜘蛛侧交付件。
    """
    url = f"{SNAPSHOT_BASE}/deleted_ids.csv.gz"
    cache_path = CACHE_DIR / "deleted_ids.csv.gz"
    _download_gz_to_cache(url, cache_path)
    out: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    with gzip.open(cache_path, "rt", encoding="utf-8") as f:
        for i, line in enumerate(f):
            wid = line.strip()
            if not wid:
                continue
            out.append({
                "work_id": wid,
                "status": "withdrawn",
                "source": SOURCE,
                "scraped_at": now,
            })
            if limit and len(out) >= limit:
                break
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 主入口：IndustryDataProvider.dispatch → 此函数
# ──────────────────────────────────────────────────────────────────────────────

def run_education_openalex(
    limit: int = 100,
    phase: str = "skeleton",
    education_source: str = "subfield_3304",
    date_prefix: str | None = None,
) -> list[dict]:
    """教育 OpenAlex 子域入库主入口。

    Args:
        limit: 最大返回行数（测试用；生产跑全量时传大数或 None）
        phase: "skeleton" | "oa_fulltext" | "incremental" | "deleted_check"
        education_source: "subfield_3304" | "topic_T13106" | "topic_T11875" | "topic_T12171"
        date_prefix: incremental 专用的显式分区日期（"YYYY-MM-DD"，见 _run_incremental）
    """
    if education_source not in TOPIC_SOURCE_MAP.values():
        raise ValueError(f"education_source must be one of {list(TOPIC_SOURCE_MAP.values())}")

    if phase == "skeleton":
        return _run_skeleton(limit, education_source)
    elif phase == "oa_fulltext":
        return _run_oa_fulltext(limit, education_source)
    elif phase == "incremental":
        return _run_incremental(limit, education_source, date_prefix)
    elif phase == "deleted_check":
        return _deleted_ids(limit)
    else:
        raise ValueError(
            f"Unknown phase: {phase}. Must be one of: skeleton, oa_fulltext, incremental, deleted_check"
        )


# 兼容旧命令名（manifest.yaml 用 run_education_openalex_skeleton）
def run_education_openalex_skeleton(limit: int = 100, phase: str = "skeleton",
                                    date_prefix: str | None = None) -> list[dict]:
    """兼容旧命令：默认跑 skeleton，仅 Education 子域。"""
    return run_education_openalex(limit=limit, phase=phase,
                                  education_source="subfield_3304", date_prefix=date_prefix)


if __name__ == "__main__":
    # 手工测试：python -m spiders.education-openalex.spider
    import sys
    test_limit = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(f"Test run: limit={test_limit}, phase=skeleton, source=subfield_3304")
    rows = run_education_openalex_skeleton(limit=test_limit)
    print(f"Fetched {len(rows)} rows")
    if rows:
        print(json.dumps(rows[0], ensure_ascii=False, indent=2)[:500])