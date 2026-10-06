"""spiders/oecd-cli —— OECD 复合先行指标（CLI）月度序列取数。

数据表面（公开 SDMX 2.1 REST，免 key；工单 brief「侦察簿 W1-A 深潜」）：

- ``GET https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI/<key>
  ?format=csvfilewithlabels``
  dataflow = ``OECD.SDD.STES:DSD_STES@DF_CLI``；种子 key
  ``CHN.M.LI.IX._Z.NOR.IX._Z.H``（中国 CLI，月度，指数，季调归一）。
- **CSV 导出必须 ``format=csvfilewithlabels``**（带码值+标签双列）。
- **key 与 dataflow 用 ``/`` 分隔**（SDMX 2.1 REST 的 ``data/<flowRef>/<key>`` 形态）；
  用逗号连写会把 key 落到 flowRef 的 version 位，实测报
  ``Invalid version string provided``（见 README 偏差记录）。

坑位与容错（brief.notes 逐条落实）：

- 上游行**不按期次排序**（实测首行 2012-09、末行 2012-08）——本单元按
  ``TIME_PERIOD`` 升序排序后再截 ``limit``。
- ``limit`` 语义 = 返回行数上限：升序后保留**最近** ``limit`` 期（生产取最新）；
  golden 重放用小 ``limit`` 锚固定历史月值，结果恒定。
- 空响应/无数据列 → 该 key 失败并留痕；**全部 key 失败抛 ``RuntimeError``**
  （失败即红，不静默返回空列表）。
- OBS_VALUE 为浮点（如 ``96.25574``），归一为 round 6 位小数。
- 站点拒绝缺省 Python UA（实测 403）：只带常规浏览器 UA，不做指纹伪装与频次对抗。

工单：``reports/health-tickets/20261006-oecd-cli-ac318af6.yaml``（``kind=generate``）
"""
from __future__ import annotations

import csv
import io
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE = "oecd-cli"
DATA_URL = "https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI"

# 站点拒绝缺省 Python UA（实测 Python-urllib/3.x → 403；curl 默认 UA 可通），
# 只带常规浏览器 UA（nmc-weather 同款策略），不做指纹伪装与频次对抗。
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv, */*",
}

# 种子 key：中国 CLI（月度，指数口径，季调归一）。可扩各国：换首段 REF_AREA 即可。
SEED_KEYS = ("CHN.M.LI.IX._Z.NOR.IX._Z.H",)
TIMEOUT = 60  # 秒；CSV 全史约几百行，超时即跳过该 key（不重试、不加频次）

_CONTEXT_COLS = ("REF_AREA", "FREQ", "MEASURE", "UNIT_MEASURE", "ADJUSTMENT", "TRANSFORMATION")


def _get_text(url: str) -> str:
    """GET 一个 CSV 端点（仅 https 固定主机；无鉴权、无重试放大）。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    return raw.decode("utf-8-sig", "replace")  # 兼容可能的 BOM


def _num(value) -> float | None:
    """OBS_VALUE 归一：浮点字符串 → round 6 位；空/无法解析 → ``None``（缺测）。"""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return round(float(text), 6)
    except ValueError:
        return None


def fetch_series(key: str) -> list[dict]:
    """取一条 CLI 序列的全部月度 obs；空响应/无有效行抛 ``ValueError``。"""
    query = urllib.parse.urlencode({"format": "csvfilewithlabels"})
    url = f"{DATA_URL}/{key}?{query}"
    reader = csv.DictReader(io.StringIO(_get_text(url)))
    if not reader.fieldnames or "TIME_PERIOD" not in reader.fieldnames:
        raise ValueError(f"key {key}: CSV has no TIME_PERIOD column (got {reader.fieldnames})")

    rows: list[dict] = []
    for rec in reader:
        period = str(rec.get("TIME_PERIOD", "")).strip()
        if not period:
            continue
        rows.append({
            "period": period,  # 月期格式，如 2020-01
            "value": _num(rec.get("OBS_VALUE")),
            "series_key": key,
            **{c.lower(): (str(rec.get(c, "")).strip() or None) for c in _CONTEXT_COLS},
            "url": url,
            "source": SOURCE,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })
    if not rows:
        raise ValueError(f"key {key}: response parsed but zero data rows (key may be invalid)")
    rows.sort(key=lambda r: r["period"])  # 上游不保证有序，必须显式排序
    return rows


def run_oecd_cli(limit: int = 100, key: str | None = None) -> list[dict]:
    """取 CLI 序列月度行；全部 key 失败 → 抛 ``RuntimeError``（失败即红）。

    - ``key`` 缺省用种子 ``CHN.M.LI.IX._Z.NOR.IX._Z.H``（可传各国 key 扩展）；
    - 行按期次升序排列，返回最近 ``limit`` 行（生产取最新）；
    - golden 重放传小 ``limit`` 并锚固定历史月值（2020-01），结果恒定。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    keys = [key] if key else list(SEED_KEYS)
    rows: list[dict] = []
    errors: list[str] = []
    for k in keys:
        try:
            rows.extend(fetch_series(k))
        except Exception as exc:  # noqa: BLE001 - 单序列失败只跳过并留痕
            errors.append(f"{k}: {type(exc).__name__}: {exc}")
    if not rows:
        raise RuntimeError("oecd-cli: 全部序列取数失败 — " + "; ".join(errors)[:300])
    rows.sort(key=lambda r: r["period"])
    return rows[-limit:]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/oecd-cli/spider.py
    print(json.dumps(run_oecd_cli(limit=3), ensure_ascii=False, indent=2))
