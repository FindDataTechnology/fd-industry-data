"""spiders/statcan-bulk —— 加拿大统计局 bulk CSV（ZIP）取数：新房价指 NHPI。

数据表面（官方 bulk ZIP，公开免鉴权；工单 brief）：

- ``GET https://www150.statcan.gc.ca/n1/tbl/csv/18100205-eng.zip``
  → ZIP 内 ``18100205.csv``（长表 REF_DATE×GEO×指标，月度，1981-01 起）+ MetaData。
- 表目录核对（``getAllCubesList`` 实测）：18-10-0205-01 = New housing price index,
  monthly（现行，数据至 2026-08；CANSIM 327-0056）。brief 点名的建筑许可现行表
  34-10-0292-01 bulk ZIP 达 368 MB，超出本单元承载（详见 README），未纳入。

口坑（brief.notes 逐条落实）：

- WDS REST（POST getAllCubesList 等）实测不可用（HTTP 405/空响应），走 bulk ZIP；
  GET 形态可用但本单元不依赖它，productId 以实测 MetaData 为准。
- CSV 是 **UTF-8 with BOM**，须以 ``utf-8-sig`` 解码；``VALUE`` 空串（缺测）→ ``None``。
- 行序 = REF_DATE 升序（新月份**追加**在尾部），头部行是固定历史期
  （1981-01 Canada Total (house and land) = 38.2，Index 201612=100），跨期稳定 →
  ``limit`` 小值时结果恒定，golden 锚固定表固定月值。
- 容错：ZIP 下载/解包/解析全失败 → ``RuntimeError``（失败即红），不静默返回空列表；
  只带常规浏览器 UA，不做频次对抗。

工单：``reports/health-tickets/20261006-statcan-bulk-2bf4fdf6.yaml``（``kind=generate``）
"""
from __future__ import annotations

import csv
import io
import json
import urllib.request
import zipfile
from datetime import datetime, timezone

SOURCE = "statcan-bulk"
BASE_URL = "https://www150.statcan.gc.ca/n1/tbl/csv/{pid}-eng.zip"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/zip, */*",
}
TIMEOUT = 120  # 秒；NHPI ZIP 约 355 KB（解包 9.3 MB），公网常态内
# 参数白名单：productId 以表目录为准（工单 brief）；现行月度 NHPI = 18-10-0205-01
DEFAULT_PRODUCT_ID = "18100205"


def _num(value):
    """数值归一：空串（缺测占位）/无法解析 → ``None``。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def run_statcan_bulk(limit: int = 100, product_id: str = DEFAULT_PRODUCT_ID) -> list[dict]:
    """取 StatCan bulk ZIP 内主 CSV 的至多 ``limit`` 行；全链路失败 → ``RuntimeError``。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []
    pid = str(product_id).replace("-", "")  # 18-10-0205 → 18100205（bulk 命名口径）
    if not pid.isdigit() or len(pid) != 8:
        raise ValueError(f"statcan-bulk: productId 形态非法: {product_id!r}（应为 8 位数字）")

    url = BASE_URL.format(pid=pid)
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
            raw = resp.read()
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            member = f"{pid}.csv"
            if member not in zf.namelist():
                raise RuntimeError(f"statcan-bulk: ZIP 内无 {member}（members={zf.namelist()[:5]}…）")
            text = zf.read(member).decode("utf-8-sig", "replace")
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - 单站失败即红
        raise RuntimeError(f"statcan-bulk: bulk ZIP 取数/解包失败 {url}: {type(exc).__name__}: {exc}") from exc

    rows: list[dict] = []
    for rec in csv.DictReader(io.StringIO(text)):
        # NHPI 表结构（实测）：REF_DATE/GEO/DGUID/<指标维度>/UOM/.../VALUE/...
        # 指标维度列名随表而异（NHPI 为 "New housing price indexes"），按第 4 列取。
        fields = list(rec.keys())
        index_type = rec.get(fields[3]) if len(fields) > 3 else None
        rows.append(
            {
                "ref_date": rec.get("REF_DATE") or None,
                "geography": rec.get("GEO") or None,
                "series": index_type or None,
                "uom": rec.get("UOM") or None,
                "value": _num(rec.get("VALUE")),
                "url": url,
                "source": SOURCE,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if len(rows) >= limit:
            break
    if not rows:
        raise RuntimeError(f"statcan-bulk: bulk CSV 解析 0 行 — 失败即红: {url}")
    return rows[:limit]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/statcan-bulk/spider.py
    print(json.dumps(run_statcan_bulk(limit=3), ensure_ascii=False, indent=2))
