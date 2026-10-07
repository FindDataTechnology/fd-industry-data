"""spiders/census-m3 —— 美国制造商出货/库存/订单月度（Census EITS：M3）取数。

数据表面（api.census.gov 公开 REST，key= 查询参数传递；工单 brief.notes）：

- ``GET https://api.census.gov/data/timeseries/eits/m3``（Manufacturers'
  Shipments, Inventories, and Orders，1992 起）
- 返回二维数组：首行表头 ``[cell_value, data_type_code, time_slot_id, time,
  category_code, seasonally_adj, us]``，其后每行一条指标值。

实测请求参数（只发这些，2026-10-07 直连冒烟）：

- ``get=cell_value,data_type_code,time_slot_id`` + ``for=us:*``
- ``time=2020-01``（固定历史月，跨期复演稳定）
- ``category_code=MTM``（制造业总计 Total Manufacturing；**实测勘误：m3 的
  category_code 是助记码不是 NAICS——``334413`` 等六位 NAICS 一律 HTTP 204
  空响应**，详见 README 勘误表）
- ``seasonally_adj=no``（不经季调原值）

口坑（brief.notes 逐条落实）：

- **必填谓词 category_code + seasonally_adj**：缺任一上游即 400
  ``error: missing required variable/predicate: ...``（实测）。
- **无 key / key 非法时上游 302 → HTML "Missing Key" 错误页（HTTP 200 形态）**：
  响应体首字符必须为 ``[``，否则显式报错——绝不允许静默降级为无 key 通道裸跑。
- key 一律从 ``os.environ["CENSUS_API_KEY"]`` 读取；缺失/空值 → ``RuntimeError``
  （失败即红），**绝不硬编码/落文件**（与 census-eits 共用同一把 Census key）。
- **golden 锚固定 NAICS（brief 原意）→ 落地为固定 category_code MTM 固定历史月**：
  锚 ``MTM/VS``（出货）与 ``MTM/MI``（库存）、``MTM/NO``（新订单）的 2020-01
  实测值（构建时实况冻结；若上游年度基准修订触及该月，需人工复锚）。
- ``cell_value`` 数值化：可解析为 float 则转 float，否则原样字符串透出。
- 容错：拉取失败 / 零可用行 → 抛 ``RuntimeError``（失败即红，不静默返回空列表）。

工单：``reports/health-tickets/20261007-census-m3-a7211ee6.yaml``（``kind=generate``）
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

SOURCE = "census-m3"
M3_URL = "https://api.census.gov/data/timeseries/eits/m3"
TIME = "2020-01"      # 固定历史月（定稿值，跨期复演稳定；锚值见 golden/）
CATEGORY = "MTM"      # 制造业总计（助记码；NAICS 六位码不可用，实测 204）

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
TIMEOUT = 30  # 秒；单 category 单月约 1 KB，超时即失败（失败即红，不重试放大）


def _api_key() -> str:
    """从环境读 Census key；缺失/空 → RuntimeError（失败即红，不静默降级）。"""
    key = (os.environ.get("CENSUS_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "census-m3: 缺少环境变量 CENSUS_API_KEY（key= 查询参数传递；"
            "拒绝无 key 通道裸跑——上游会 302 到 HTML Missing Key 页）"
        )
    return key


def _get_rows(url: str) -> list[list]:
    """取二维数组载荷；**首字符必须 ``[``**（无 key/超限时上游 302 → HTML 错误页，
    HTTP 仍可能为 200 形态——绝不允许当数据吞下）。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    text = raw.decode("utf-8", "replace").lstrip()
    if not text.startswith("["):
        head = text[:120].replace("\n", " ")
        raise RuntimeError(
            "census-m3: 响应非 JSON 数组（疑似无 key/超限的 HTML 错误页）："
            f"{head!r}"
        )
    payload = json.loads(text)
    if not (isinstance(payload, list) and len(payload) >= 2
            and isinstance(payload[0], list)):
        raise RuntimeError("census-m3: 载荷无表头行（上游口径已变，转人工）")
    return payload


def _num(value):
    """cell_value 归一：可解析为 float 则转 float，脏值原样字符串透出。"""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None if value is None else str(value)


def run_census_m3(limit: int = 100) -> list[dict]:
    """取制造业总计（MTM）2020-01 未季调出货/库存/订单指标行，至多 ``limit`` 行。

    拉取失败、HTML 错误页或零可用行 → 抛 ``RuntimeError``（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    key = _api_key()
    url = (
        f"{M3_URL}?get=cell_value,data_type_code,time_slot_id&for=us:*"
        f"&time={TIME}&category_code={CATEGORY}&seasonally_adj=no&key={key}"
    )
    try:
        payload = _get_rows(url)
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - 拉取失败 → 失败即红
        raise RuntimeError(f"census-m3: 拉取失败 {type(exc).__name__}: {exc}") from exc

    # 行内 url 剔除 key（行数据会落盘，key 绝不入文件）；请求仍用带 key 的真 URL
    display_url = url.replace(f"&key={key}", "")
    header = [str(col) for col in payload[0]]
    rows: list[dict] = []
    for line in payload[1:]:
        if not isinstance(line, list) or len(line) != len(header):
            continue
        rec = dict(zip(header, line))
        rows.append({
            "time": rec.get("time"),
            "category_code": rec.get("category_code"),
            "data_type_code": rec.get("data_type_code"),
            "seasonally_adj": rec.get("seasonally_adj"),
            "cell_value": _num(rec.get("cell_value")),
            "time_slot_id": rec.get("time_slot_id"),
            "url": display_url,
            "source": SOURCE,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })
    if not rows:
        raise RuntimeError("census-m3: 取数为空（上游空窗或口径变更，转人工）")
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/census-m3/spider.py
    print(json.dumps(run_census_m3(limit=3), ensure_ascii=False, indent=2))
