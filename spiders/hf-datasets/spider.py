"""spiders/hf-datasets —— HuggingFace Hub 每月新增数据集计数（月度周期信号）。

数据表面（Hub 公开 listing API，**免 key、免鉴权**；批次二 Wave C 侦察簿 ★接，经代理复测）：

- ``GET https://huggingface.co/api/datasets?limit=100&sort=createdAt&direction=-1``
  返回**最新创建**的公开数据集（compact 对象，含不可变 ``createdAt`` ISO 时间戳），
  翻页靠响应 ``Link`` 头 ``rel="next"``（opaque cursor，不可自己拼）。
- **需代理出口**：huggingface.co 主站 API 从中国大陆网络直连不可达，单元依赖
  运行环境的 ``HTTPS_PROXY``/``HTTP_PROXY`` 环境变量（标准库 urllib 默认行为，
  本单元**不硬编码代理地址**）。兜底主机 ``hf-mirror.com`` 可直连：
  主机序 ``huggingface.co → hf-mirror.com``，一个主机整轮翻页失败才换下一个。

信号选择理由（数据集口径授权：批次二 Wave C）：Hub 不提供原生的按月聚合端点，
本单元取「**按月新增数据集数**」——`createdAt` 逐条不可变、listing 免费免 key、
月度粒度稳定，是 Hub 供给侧活动的周期性信号。用固定页数（默认 3 页 ×100 条）
的最新样本分桶到 ``YYYY-MM``，`sampled_total` 记样本量，计数为**样本口径**而非
全站口径（README 详述）。

口坑：

- **翻页 cursor 不透明**：必须跟随 ``Link: <...>; rel="next"``，不能自拼 offset——
  本单元逐页解析 Link 头。
- **计数类 golden 不锚当期总数**：每月计数随样本窗口滚动而变，golden 只锚结构
  常量（``source`` 字段 + ``min_rows``），绝不锚具体月份数值（README 论证）。
- **错主机/风控页会返回非 JSON**：统一按解析失败计主机失败，换下一主机；
  **全部主机失败抛 ``RuntimeError``**（失败即红，绝不静默返回空列表）。
- 个别条目缺/坏 ``createdAt``：跳过并计 ``malformed_skipped``（留痕，不致命）。
- ``limit`` 语义 = 返回行数上限：期次升序后保留**最近** ``limit`` 个月（生产取最新）。
- 免 key、免 UA 伪装；不做指纹伪装与频次对抗，遇风控升级按协议转人工，不逆向。
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone

SOURCE = "hf-datasets"
API_PATH = "/api/datasets"
# 主机序：主站需代理出口；hf-mirror.com 为直连兜底（同构 Hub API 反代）。
HOSTS = ("huggingface.co", "hf-mirror.com")
PAGE_SIZE = 100          # listing 单页条数（官方上限内）
DEFAULT_PAGES = 3        # 默认采样 3 页 ≈ 最新 300 个数据集
MAX_PAGES = 10           # 防御上限，避免调用方误传大值放大请求
TIMEOUT = 30             # 秒/请求；超时即该主机失败（不重试、不加频次）
_UA = "fd-industry-data/2.0 (hf-datasets; industry data pipeline)"

_LINK_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')
_DATE_PREFIX_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def _get_json(url: str) -> tuple[object, str | None]:
    """GET 一个端点并解析 JSON；返回 (data, Link 头)。urllib 尊重环境代理。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        return json.loads(resp.read().decode("utf-8")), resp.headers.get("Link")


def _first_page_url(host: str) -> str:
    """最新创建优先的 listing 首页 URL（createdAt 不可变，排序键稳定）。"""
    return f"https://{host}{API_PATH}?limit={PAGE_SIZE}&sort=createdAt&direction=-1"


def _next_link(header: str | None) -> str | None:
    """从 Link 头取 rel="next"（可能多段逗号分隔；无则 None=已到尾页）。"""
    if not header:
        return None
    m = _LINK_NEXT_RE.search(header)
    return m.group(1) if m else None


def _fetch_sample(pages: int) -> tuple[list[dict], str, str]:
    """取最新 ``pages`` 页 listing；逐主机整轮尝试，**全部主机失败抛 RuntimeError**。

    返回 (原始条目列表, 服务主机, 首页 URL)。单主机任一页失败即弃用该主机
    （all-or-nothing，避免半截样本悄悄通过）。
    """
    errors: list[str] = []
    for host in HOSTS:
        try:
            first_url = _first_page_url(host)
            url: str | None = first_url
            items: list[dict] = []
            for _ in range(pages):
                if not url:
                    break
                data, link = _get_json(url)
                if not isinstance(data, list):
                    raise ValueError(f"{host}: expected JSON array, got {type(data).__name__}")
                items.extend(entry for entry in data if isinstance(entry, dict))
                url = _next_link(link)
            if not items:
                raise ValueError(f"{host}: empty listing ({pages} page(s))")
            return items, host, first_url
        except Exception as exc:  # noqa: BLE001 - 单主机失败换兜底，最后统一红
            errors.append(f"{host}: {type(exc).__name__}: {exc}")
    raise RuntimeError("hf-datasets: 全部主机取数失败 — " + "; ".join(errors)[:400])


def _month_of(entry: dict) -> str | None:
    """从 ``createdAt``（如 ``2026-10-07T12:28:07.000Z``）取 ``YYYY-MM``；坏值返 None。"""
    created = entry.get("createdAt")
    if not isinstance(created, str):
        return None
    m = _DATE_PREFIX_RE.match(created.strip())
    if not m:
        return None
    try:
        datetime.strptime(m.group(0), "%Y-%m-%d")
    except ValueError:
        return None
    return f"{m.group(1)}-{m.group(2)}"


def run_hf_datasets(limit: int = 100, pages: int = DEFAULT_PAGES) -> list[dict]:
    """取 Hub 每月新增数据集计数行；全部主机失败 → 抛 ``RuntimeError``（失败即红）。

    - 采样：最新 ``pages`` 页 × :data:`PAGE_SIZE` 条（默认 3×100），按 ``createdAt``
      分桶到 ``YYYY-MM``（UTC），一行一个月；
    - ``limit`` 语义 = 行数上限：期次升序后保留**最近** ``limit`` 个月；
    - 计数为**样本口径**（``sampled_total`` 记样本量），非全站口径；
    - golden 重放传 ``pages=1``，只锚结构常量，重放恒定。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    try:
        pages = min(max(int(pages), 1), MAX_PAGES)
    except (TypeError, ValueError):
        raise ValueError(f"pages must be an int, got {pages!r}") from None
    if limit <= 0:
        return []

    items, host, first_url = _fetch_sample(pages)
    buckets: dict[str, int] = {}
    malformed = 0
    for entry in items:
        month = _month_of(entry)
        if month is None:
            malformed += 1
            continue
        buckets[month] = buckets.get(month, 0) + 1
    if not buckets:
        raise RuntimeError(
            f"hf-datasets: 采样 {len(items)} 条均无可解析 createdAt（malformed={malformed}）")

    now = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "period": month,                 # YYYY-MM（UTC，按 createdAt）
            "new_datasets": count,           # 样本口径：该月新增公开数据集数
            "sampled_total": len(items),     # 本轮采样总量（行级常量，供对账）
            "malformed_skipped": malformed,  # 缺/坏 createdAt 被跳过的条数（行级常量）
            "endpoint_host": host,           # 实际服务主机（主站或 hf-mirror 兜底）
            "url": first_url,                # 首页请求 URL，行级来源可追溯
            "source": SOURCE,
            "scraped_at": now,
        }
        for month, count in sorted(buckets.items())
    ]
    return rows[-limit:]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/hf-datasets/spider.py
    print(json.dumps(run_hf_datasets(limit=10, pages=1), ensure_ascii=False, indent=2))
