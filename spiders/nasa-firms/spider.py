"""spiders/nasa-firms —— NASA FIRMS 全球 24h 活跃火点（S-NPP VIIRS C2）取数。

数据表面（公开 CSV 快照，免 key；侦察簿 W1-I，见工单 brief.notes；MAP_KEY 仅
 FIRMS API 才需要，本单元走静态产品文件，不需要）：

- ``GET https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/
  SUOMI_VIIRS_C2_Global_24h.csv`` → CSV（约 8.7 MB，10 万+ 行，每日滚动更新）

实测列口径（2026-10-07 直连冒烟，13 列固定）：
``latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,
version,bright_ti5,frp,daynight``

口坑（brief.notes 逐条落实）：

- **每日滚动**：``acq_date``/行集/计数每天都在变——golden 只锚列名集合（经常量列）
  + ``instrument`` 常量 + ``min_rows``，绝不锚日期/计数/坐标。
- **``instrument`` 列上游不存在**：产品身份是 ``SUOMI_VIIRS_C2``（Suomi-NPP 载
  VIIRS 传感器），本单元补常量列 ``instrument="VIIRS"``（`satellite="N"` 即
  Suomi NPP，亦为该产品常量）；工单 expectations 写的 ``brightness`` 是 MODIS
  命名，VIIRS C2 实际为 ``bright_ti4``/``bright_ti5``，按实相透出不改名。
- ``acq_time`` 是 HHMM（如 ``0117``）——**保留字符串**，避免丢前导零。
- 容错：单行解析失败跳过留痕；CSV 拉取失败 / 表头缺列 / 全部行失败 → 抛
  ``RuntimeError``（失败即红，不静默返回空列表）。

工单：``reports/health-tickets/20261006-nasa-firms-1bd9e18a.yaml``（``kind=generate``）
"""
from __future__ import annotations

import csv
import io
import json
import sys
import urllib.request
from datetime import datetime, timezone

SOURCE = "nasa-firms"
CSV_URL = (
    "https://firms.modaps.eosdis.nasa.gov/data/active_fire/"
    "suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Global_24h.csv"
)
INSTRUMENT = "VIIRS"  # 产品身份 SUOMI_VIIRS_C2 的传感器常量（上游 CSV 无此列）

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
TIMEOUT = 120  # 秒；快照约 8.7 MB，给足带宽余量，不重试、不加频次


def _fetch_csv() -> str:
    """拉取快照 CSV 文本（仅固定 https 产品文件；无鉴权、无重试放大）。"""
    req = urllib.request.Request(CSV_URL, headers=HEADERS)
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


def run_nasa_firms(limit: int = 100) -> list[dict]:
    """取全球 24h 火点快照前 ``limit`` 行；快照不可用 → 抛 ``RuntimeError``（失败即红）。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    text = _fetch_csv()
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []
    missing = [c for c in FLOAT_COLS + STR_COLS if c not in header]
    if missing:
        raise RuntimeError(f"nasa-firms: CSV 表头缺实测列 {missing}（上游布局已变，转人工）")

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
            "url": CSV_URL,
            "source": SOURCE,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })
    if skipped:
        print(f"{SOURCE}: skipped {skipped} malformed row(s)", file=sys.stderr)
    if not rows:
        raise RuntimeError("nasa-firms: 快照无可用数据行（上游空窗或布局变更，转人工）")
    return rows


if __name__ == "__main__":  # 冒烟：python3 spiders/nasa-firms/spider.py
    print(json.dumps(run_nasa_firms(limit=2), ensure_ascii=False, indent=2))
