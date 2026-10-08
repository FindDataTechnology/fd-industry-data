"""spiders/ourairports —— OurAirports 机场维表类型计数快照。

数据表面（OurAirports data，**免 key、免鉴权、直连可达**；2026-10-08 实测
直连 HTTP 200，约 12.7 MB）：

- ``GET https://davidmegginson.github.io/ourairports-data/airports.csv``
  全量机场维表 CSV，19 列（id, ident, type, name, latitude_deg, longitude_deg,
  elevation_ft, continent, iso_country, iso_region, municipality,
  scheduled_service, icao_code, iata_code, gps_code, local_code, home_link,
  wikipedia_link, keywords），每行一个机场；``type`` 列取值
  large_airport / medium_airport / small_airport / heliport / closed /
  seaplane_base / balloonport。

- **必须流式解析**：12.7 MB 全量 ``read()`` 会整块进内存——本单元用
  ``io.TextIOWrapper`` 包住响应流，``csv.DictReader`` 逐行读，行读完即弃，
  常驻内存只有一行。

信号口径（批次三直建）：机场维表逐行明细无周期性，取 **类型计数快照**——
按 ``type`` 分桶计数（每类一行 `total`）+ `total_airports` 总计（行级常量
随每行带出，对账 `total_airports == sum(type totals)`）。维表月度重建，
快照间对比即可看出各类型机场池的增减。

口坑：

- **维表月更**：上游约每月重建，计数是「当期快照」——golden 只锚结构常量
  （``source`` + ``period`` 格式 + ``min_rows``），绝不锚当期计数。
- **`period` 是快照日**：维表无观测时间戳，行级 ``period`` = 抓取日 UTC 日期，
  语义是「该日维表状态」，不是业务发生日（README 论证）。
- **空 type 行**：跳过并计入 `malformed_skipped`（不进 type 桶、不计入
  `total_airports`，保持两口径可对账）。
- **全部失败抛 ``RuntimeError``**（失败即红，绝不静默返回空列表）。
- 免 key、免 UA 伪装；不做指纹伪装与频次对抗，遇风控升级按协议转人工，不逆向。
"""
from __future__ import annotations

import csv
import io
import urllib.request
from datetime import datetime, timezone

SOURCE = "ourairports"
ENDPOINT = "https://davidmegginson.github.io/ourairports-data/airports.csv"
TIMEOUT = 60                      # 秒/请求；12.7 MB 下载给足超时（不重试、不加频次）
_UA = "fd-industry-data/2.0 (ourairports; industry data pipeline)"


def _count_types() -> tuple[dict[str, int], int, int]:
    """流式下载并逐行统计 type 计数。

    Returns:
        (type→计数, 总计 total_airports, 空 type 行数 malformed_skipped)。

    Raises:
        RuntimeError: 请求失败、响应不是可解析 CSV 或零有效行（失败即红）。
    """
    req = urllib.request.Request(
        ENDPOINT, headers={"User-Agent": _UA, "Accept": "text/csv"})
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)  # noqa: S310 (固定 https 主机)
    except Exception as exc:  # noqa: BLE001 —— 端点级失败即红，统一包成 RuntimeError
        raise RuntimeError(
            f"ourairports: 请求失败 {ENDPOINT}: {type(exc).__name__}: {exc}") from exc

    counts: dict[str, int] = {}
    total = 0
    malformed = 0
    header_seen = False
    try:
        # 逐行流式：绝不 resp.read() 全量（12.7 MB 防 OOM）
        with io.TextIOWrapper(resp, encoding="utf-8", errors="replace") as text:
            reader = csv.DictReader(text)
            if not reader.fieldnames or "type" not in reader.fieldnames:
                raise RuntimeError(
                    f"ourairports: CSV 表头无 type 列（{reader.fieldnames}）——schema 已变")
            header_seen = True
            for row in reader:
                airport_type = (row.get("type") or "").strip()
                if not airport_type:
                    malformed += 1
                    continue
                counts[airport_type] = counts.get(airport_type, 0) + 1
                total += 1
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 解析中途断流/坏行
        raise RuntimeError(
            f"ourairports: CSV 流式解析失败: {type(exc).__name__}: {exc}") from exc
    finally:
        resp.close()

    if not header_seen:
        raise RuntimeError(f"ourairports: 响应为空或不可解析（{ENDPOINT}）")
    if total == 0:
        raise RuntimeError(
            f"ourairports: 0 行有效 type 观测（{ENDPOINT}）——schema 已变或上游空表")
    return counts, total, malformed


def run_ourairports(limit: int = 100, **_) -> list[dict]:
    """取 OurAirports 机场维表并输出类型计数快照。

    Args:
        limit: 返回行数上限；按 total 降序保留前 ``limit`` 个类型
            （当前全类型 7 个，默认 100 即全量）。

    Returns:
        每行含 period（快照日）+ airport_type/total + total_airports 等行级常量。

    Raises:
        RuntimeError: 端点不可达、schema 已变或零有效行（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 1000))

    counts, total, malformed = _count_types()

    period = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    rows: list[dict] = []
    for airport_type, cnt in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        rows.append({
            "period": period,
            "airport_type": airport_type,
            "total": cnt,
            "total_airports": total,          # 行级常量，对账 sum(type totals)
            "malformed_skipped": malformed,   # 行级常量，空 type 行数
            "url": ENDPOINT,
            "source": SOURCE,
            "scraped_at": scraped_at,
        })

    if not rows:
        raise RuntimeError(f"ourairports: 取到 0 行可输出数据（{ENDPOINT}）")

    return rows[:limit]                       # total 降序，保留前 limit 个类型


if __name__ == "__main__":
    import json as _json
    out = run_ourairports()
    print(_json.dumps(out, ensure_ascii=False, indent=2))
