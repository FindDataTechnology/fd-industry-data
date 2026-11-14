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
SUOMI_URL = (
    "https://firms.modaps.eosdis.nasa.gov/data/active_fire/"
    "suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Global_24h.csv"
)
# 候选平台阶梯（按优先级）。上游按平台发布同名 CSV，某个平台停更/发空时换下一个
# 活着的平台，而不是让整条源天天失败。
#
# 2026-11-10 实测（这就是加阶梯的原因）：
#   S-NPP(SUOMI)  24h → 200 但只有表头；同产品 7d → 35,261 行，**全部是 2026-11-02**，
#                 而文件本身今天 04:53 还在重新生成 —— 停更 8 天，但「200 + 有行」
#                 照样成立。只看有没有行会静默灌进 8 天前的数据。
#   NOAA-20(N20)  24h → 67,702 行，acq_date = 11-09/11-10（当日）
#   NOAA-21(N21)  24h → 42,970 行，acq_date = 11-09
PLATFORMS = (
    ("N20", "https://firms.modaps.eosdis.nasa.gov/data/active_fire/"
            "noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_24h.csv"),
    ("N21", "https://firms.modaps.eosdis.nasa.gov/data/active_fire/"
            "noaa-21-viirs-c2/csv/J2_VIIRS_C2_Global_24h.csv"),
    ("N",   SUOMI_URL),
)
# 快照里最新 acq_date 距今超过这么多天 → 判为「陈」，换下一个平台。
# 24h 窗口天然跨两天（UTC 日界），2 天留一格余量，再宽就会把停更当正常。
MAX_SNAPSHOT_AGE_DAYS = 2
INSTRUMENT = "VIIRS"  # VIIRS 传感器常量（上游 CSV 无此列；各平台的 satellite 码不同）

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


def _fetch_csv(url: str) -> str:
    """拉取快照 CSV 文本（仅固定 https 产品文件；无鉴权、无重试放大）。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        return resp.read().decode("utf-8", "replace")


def _snapshot_freshness(text: str) -> tuple[int, str | None]:
    """快照里 (可用行数, 最新 acq_date)。只读两列，不做完整解析。"""
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []
    if "acq_date" not in header:
        return 0, None
    rows = 0
    newest: str | None = None
    for raw in reader:
        if not _text(raw.get("acq_date")):
            continue
        rows += 1
        date = str(raw["acq_date"]).strip()
        if newest is None or date > newest:      # yyyy-mm-dd 字典序即时序
            newest = date
    return rows, newest


def _snapshot_age_days(newest: str | None, now: datetime) -> int | None:
    if not newest:
        return None
    try:
        stamp = datetime.strptime(newest, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (now.date() - stamp.date()).days


def _pick_snapshot(now: datetime | None = None) -> tuple[str, str]:
    """按 ``PLATFORMS`` 顺序挑第一份**有行且不陈**的快照，返回 (url, 文本)。

    两个失败形态都要挡，缺一不可：
    * 文件只有表头（上游把该平台发布成空）；
    * 文件有行但内容停留在几天前（文件每天重生成，行数照旧）——2026-11-10
      S-NPP 就是这种：35,261 行全是 8 天前。
    全部不可用时抛 ``RuntimeError``，逐平台写清原因（转人工）。
    """
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    for code, url in PLATFORMS:
        try:
            text = _fetch_csv(url)
        except Exception as exc:  # noqa: BLE001 - 逐个平台降级，最后统一报
            reasons.append(f"{code}: 拉取失败 {type(exc).__name__}: {exc}")
            continue
        rows, newest = _snapshot_freshness(text)
        if rows == 0:
            reasons.append(f"{code}: 快照无数据行")
            continue
        age = _snapshot_age_days(newest, now)
        if age is None:
            reasons.append(f"{code}: acq_date 不可解析（最新={newest!r}）")
            continue
        if age > MAX_SNAPSHOT_AGE_DAYS:
            reasons.append(f"{code}: 快照陈旧 {age} 天（最新 acq_date={newest}）")
            continue
        if code != PLATFORMS[0][0]:
            print(f"{SOURCE}: 首选平台不可用，改用 {code}；原因 {reasons}", file=sys.stderr)
        return url, text
    raise RuntimeError(
        f"{SOURCE}: 全部平台无可用快照（上游空窗/停更或布局变更，转人工）；{reasons}"
    )


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


def run_nasa_firms(limit: int = 100, now: datetime | None = None) -> list[dict]:
    """取全球 24h 火点快照前 ``limit`` 行；快照不可用 → 抛 ``RuntimeError``（失败即红）。

    ``now`` 是判据的时钟注入点（默认取真实当下）。``_pick_snapshot`` 本来就有这个
    参数，但入口没透传，于是用例只能吃真实时钟——写死的 acq_date 一旦超过
    ``MAX_SNAPSHOT_AGE_DAYS`` 就红在「快照陈旧」上，与它要验的东西无关
    （2026-11-14 实测：11-10 的样本 4 天后被判陈旧）。runner 侧不传，行为不变。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    url, text = _pick_snapshot(now=now)
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
            "url": url,
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
