"""spiders/nasa-firms —— NASA FIRMS 全球 24h 活跃火点（VIIRS C2）取数。

数据表面（公开 CSV 快照，免 key；MAP_KEY 仅 FIRMS API 需要，本单元走静态产品文件）：

- 首选：``GET https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_24h.csv``
  → CSV（约 9.7 MB，11 万+ 行，覆盖最近两个日窗口）
- 备选：``.../noaa-21-viirs-c2/csv/J2_VIIRS_C2_Global_24h.csv`` → 同构（13 列同名，约 12.5 万行）

**换表面依据（2026-11-09 直连实测，源自工单 20261107-nasa-firms-bd40ec7f / kind=repair /
category=fallback）**：原表面 ``suomi-npp-viirs-c2/SUOMI_VIIRS_C2_Global_24h.csv``
自 2026-11-03 起停止供数——24h/48h 文件只剩表头（0 行），7d 文件末行停在 2026-11-02；
FIRMS 首页 NRT 分发只列 NOAA-20/NOAA-21。Suomi-NPP VIIRS C2 属产品退役（非解析缺陷、
非风控升级），改换同产品族（VIIRS C2 全球 24h）同构表面，列口径完全一致（13 列同名）。
降级策略：按 ``SURFACES`` 顺序取首个「表头合规且至少 1 行」的快照；单次运行最多 2 个 GET，
不重试、不加频次（不做频次对抗）。

实测列口径（13 列，2026-10-07 直连冒烟；2026-11-09 复测 NOAA-20/21 仍为 13 列同名）：
``latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,
version,bright_ti5,frp,daynight``

口坑（brief.notes 逐条落实 + 本轮表面切换后仍成立）：

- **每日滚动**：``acq_date``/行集/计数每天都在变——golden 只锚经常量列（`instrument`/`source`）
  + `min_rows`，绝不锚日期/计数/坐标/当期数值；`satellite`/`url` 随数据表面而变，故不锚。
- **``instrument`` 列上游不存在**：产品身份是 ``*_VIIRS_C2``（VIIRS 传感器），本单元补常量列
  ``instrument="VIIRS"``（`satellite` 恒为 ``N`` 或 ``N20``/``N21``，两者即产品常量，按实相透出不改名）；
  工单 expectations 写的 ``brightness`` 是 MODIS 命名，VIIRS C2 实际为 ``bright_ti4``/``bright_ti5``，
  按实相透出不改名。
- **``acq_time`` 是 HHMM（如 ``0100``）**——**保留字符串**，避免丢前导零。
- **表头校验**：实测 13 列缺一即视为该表面布局异常，自动降级到下一表面（不硬解）；
  所有表面均失败 → 抛 ``RuntimeError``（失败即红，不静默返回空列表）。
- **容错**：单行解析失败跳过留痕；CSV 拉取失败 / 表头缺列 / 零可用行 → 抛 ``RuntimeError``（失败即红）。
- 只带常规浏览器 UA；不做指纹伪装、不做频次对抗、无签名/验证码处理。

工单：``reports/health-tickets/20261107-nasa-firms-bd40ec7f.yaml``（``kind=repair``，
兜底整段重生成：S-NPP VIIRS C2 产品退役 → 换 NOAA-20/NOAA-21 24h 表面）
"""
from __future__ import annotations

import csv
import io
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

SOURCE = "nasa-firms"
INSTRUMENT = "VIIRS"  # 产品身份 *_VIIRS_C2 的传感器常量（上游 CSV 无此列）；产品退役/换表面后仍成立

# 数据表面（VIIRS C2 全球 24h 快照，同一产品族、13 列同构）：按序降级，取首个可用。
# 顺序即优先级（manifest.source_url 与 golden 锚都指首选）；增删或调序需同步 README。
SURFACES = (
    ("noaa-20-viirs-c2", "J1_VIIRS_C2_Global_24h.csv"),
    ("noaa-21-viirs-c2", "J2_VIIRS_C2_Global_24h.csv"),
)
CSV_URL_TMPL = (
    "https://firms.modaps.eosdis.nasa.gov/data/active_fire/"
    "{product}/csv/{name}"
)
# 首选表面（manifest.source_url / golden.url 锚对齐用）
CSV_URL = (
    CSV_URL_TMPL.format(product=SURFACES[0][0], name=SURFACES[0][1])
)

# 实测 13 列：浮点列 / 字符串列
FLOAT_COLS = ("latitude", "longitude", "bright_ti4", "scan", "track",
              "bright_ti5", "frp")
STR_COLS = ("acq_date", "acq_time", "satellite", "confidence", "version",
            "daynight")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
}
TIMEOUT = 120  # 秒；快照约 10 MB，给足带宽余量，不重试、不加频次


class _SurfaceUnusable(RuntimeError):
    """单个数据表面的快照不可用（表头缺列 / 拉取失败 / 空窗），可降级到下一表面。"""


def _fetch_csv(url: str) -> str:
    """拉取快照 CSV 文本（仅固定 https 产品文件；无鉴权、无重试放大）。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        return resp.read().decode("utf-8", "replace")


def _num(value):
    """浮点归一：空串/脏值 → ``None``。"""
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _text(value):
    """文本归一：空串 → ``None``（``acq_time`` 等保留原样字符串，含前导零）。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse(text: str, limit: int, url: str) -> tuple[list[dict], int]:
    """解析单个表面的 CSV：表头缺列 → ``_SurfaceUnusable``；返回 (行, 脏行数)。"""
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []
    missing = [c for c in FLOAT_COLS + STR_COLS if c not in header]
    if missing:
        raise _SurfaceUnusable(f"CSV 表头缺实测列 {missing}")

    rows: list[dict] = []
    skipped = 0
    for raw in reader:
        if len(rows) >= limit:
            break
        latitude = _num(raw.get("latitude"))
        longitude = _num(raw.get("longitude"))
        if latitude is None or longitude is None:  # 坐标是火点行的最小完整性约束
            skipped += 1
            continue
        rows.append({
            "latitude": latitude,
            "longitude": longitude,
            "bright_ti4": _num(raw.get("bright_ti4")),
            "bright_ti5": _num(raw.get("bright_ti5")),
            "scan": _num(raw.get("scan")),
            "track": _num(raw.get("track")),
            "frp": _num(raw.get("frp")),
            "acq_date": _text(raw.get("acq_date")),
            "acq_time": _text(raw.get("acq_time")),
            "satellite": _text(raw.get("satellite")),
            "instrument": INSTRUMENT,
            "confidence": _text(raw.get("confidence")),
            "version": _text(raw.get("version")),
            "daynight": _text(raw.get("daynight")),
            "url": url,  # 实际取数表面（随降级可能变化，行级可追溯）
            "source": SOURCE,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })
    return rows, skipped


def run_nasa_firms(limit: int = 100) -> list[dict]:
    """按 ``SURFACES`` 顺序取首个可用表面的全球 24h 火点快照前 ``limit`` 行。

    表面不可用（空窗 / 表头缺列）自动降级到下一表面；全部失败 → 抛 ``RuntimeError``
    （失败即红，不静默返回空列表）。单次最多 2 个 GET，不重试、不加频次。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    skipped_total = 0
    reasons: list[str] = []
    for product, name in SURFACES:
        url = CSV_URL_TMPL.format(product=product, name=name)
        try:
            rows, skipped = _parse(_fetch_csv(url), limit, url)
        except _SurfaceUnusable as exc:
            reasons.append(f"{product}: {exc}")
            continue
        except (urllib.error.URLError, OSError, csv.Error) as exc:
            # 网络层/解析层失败：换下一表面，不做重试放大
            reasons.append(f"{product}: {type(exc).__name__}: {exc}")
            continue
        skipped_total += skipped
        if rows:
            if skipped_total:
                print(f"{SOURCE}: skipped {skipped_total} malformed row(s)", file=sys.stderr)
            return rows
        reasons.append(f"{product}: 快照无数据行（上游空窗）")

    raise RuntimeError(
        f"{SOURCE}: 所有数据表面均不可用 -> "
        + "; ".join(reasons)
        + "（上游空窗或布局变更，转人工）"
    )


if __name__ == "__main__":  # 冒烟：python3 spiders/nasa-firms/spider.py
    print(json.dumps(run_nasa_firms(limit=2), ensure_ascii=False, indent=2))
