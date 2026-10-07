"""spiders/jp-estat —— 日本政府统计 e-Stat API（v3.0 app/json）两步流取数。

数据表面（工单 brief / 20261007-jp-estat-55b64112）：

- 第 1 步 ``GET .../getStatsList?appId=...&searchWord=...&limit=10``
  → ``GET_STATS_LIST.RESULT.STATUS == 0`` + ``DATALIST_INF.TABLE_INF[]``（``@id`` = statsDataId）
- 第 2 步 ``GET .../getStatsData?appId=...&statsDataId=...&limit=...``
  → ``GET_STATS_DATA.STATISTICAL_DATA.DATA_INF.VALUE[]``
- 鉴权：``appId=`` 查询参数（env ``ESTAT_APP_ID``，绝不硬编码；portal 注册后即时発行）
- base：``https://api.e-stat.go.jp/rest/3.0/app/json/``（直连通，实测 2026-10-07）

官方未公示数值化限速——低频分页即可；本爬虫每次调用发 1-2 个请求
（``stats_data_id`` 直供时只发 getStatsData 一个），控频由调用方负责。

实测锚（2026-10-07 实况冻结，statsDataId 固化 + 固定历史期值）：

- statsDataId ``0000150041``（人口推計 平成5年10月1日現在推計人口，updated 2025-10-03）
- 首行 ``@cat01=001 @cat02=000 @area=00000 @time=1993000000 @unit=人 → 124451938``
  （1993-10-01 日本总人口，定稿历史值，golden 重放锚；全表 87 行）

口坑：

- ``RESULT.STATUS != 0`` / 表列表空 / 数据行空 → 抛 ``RuntimeError``（失败即红）
- ``TABLE_INF`` / ``VALUE`` 单条时上游给 dict 而非 list——两形态都要接
- 值 ``$`` 为字符串；数值化成功才透出（float），抑制码（``-``/``X``/``na``）行跳过，绝不造数
- ``@time`` 原样透出（如 ``1993000000``，YYYYMMDDHHMM 语义由 meta 解释）
- ``source_url`` 中 appId 一律替换为 ``REDACTED``（凭据不落盘）

工单：``reports/health-tickets/20261007-jp-estat-55b64112.yaml``（kind=generate）
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE = "jp-estat"
BASE_URL = "https://api.e-stat.go.jp/rest/3.0/app/json"
TIMEOUT = 120  # 秒；getStatsList 全文检索实测可达 ~60s（2026-10-07 慢时段），超时即失败（失败即红，不重试放大）


def _get_json(url: str):
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(300).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - 错误体读取失败不掩盖主错误
            pass
        raise RuntimeError(f"jp-estat: HTTP {exc.code} {detail}") from exc
    except Exception as exc:  # noqa: BLE001 - 连接层失败统一失败即红
        raise RuntimeError(f"jp-estat: 拉取失败 {type(exc).__name__}: {exc}") from exc
    return json.loads(raw.decode("utf-8", "replace"))


def _as_list(value):
    """上游单条时给 dict、多条时给 list——统一成 list。"""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _num(value):
    """``$`` 字符串数值化；抑制码/脏值 → None（绝不造数）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _resolve_stats_data_id(app_id: str, search_word: str) -> str:
    """第 1 步 getStatsList：搜索词取首个 TABLE_INF 的 @id（statsDataId）。"""
    url = (f"{BASE_URL}/getStatsList?appId={app_id}"
           f"&searchWord={urllib.parse.quote(search_word)}&limit=10")
    payload = _get_json(url)
    body = payload.get("GET_STATS_LIST") if isinstance(payload, dict) else None
    result = (body or {}).get("RESULT") or {}
    status = result.get("STATUS")
    if status != 0:
        raise RuntimeError(f"jp-estat: getStatsList RESULT.STATUS={status!r} "
                           f"({result.get('ERROR_MSG', '')[:120]})")
    tables = _as_list((body.get("DATALIST_INF") or {}).get("TABLE_INF"))
    if not tables:
        raise RuntimeError(f"jp-estat: getStatsList 无 TABLE_INF（searchWord={search_word!r} 无命中，转人工）")
    stats_data_id = str(tables[0].get("@id") or "").strip()
    if not stats_data_id:
        raise RuntimeError("jp-estat: 首个 TABLE_INF 缺 @id（上游口径已变，转人工）")
    print(f"{SOURCE}: getStatsList({search_word!r}) -> statsDataId {stats_data_id}", file=sys.stderr)
    return stats_data_id


def run_jp_estat(
    limit: int = 100,
    search_word: str = "人口",
    stats_data_id: str | None = None,
) -> list[dict]:
    """两步流取数行，至多 ``limit`` 行。

    ``stats_data_id`` 直供时跳过检索步（golden 重放走此形态，锚 0000150041）；
    缺省时 getStatsList( ``search_word`` ) 首个 TABLE_INF 固化。任一步失败即抛
    ``RuntimeError``（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    app_id = os.environ.get("ESTAT_APP_ID")
    if not app_id:
        raise RuntimeError(
            "jp-estat: env ESTAT_APP_ID 未设置（secrets/fd-industry-source-keys.env 注入），拒绝裸跑"
        )

    target_id = str(stats_data_id).strip() if stats_data_id else _resolve_stats_data_id(app_id, search_word)
    url = (f"{BASE_URL}/getStatsData?appId={app_id}"
           f"&statsDataId={target_id}&limit={limit}")
    payload = _get_json(url)
    body = payload.get("GET_STATS_DATA") if isinstance(payload, dict) else None
    result = (body or {}).get("RESULT") or {}
    status = result.get("STATUS")
    if status != 0:
        raise RuntimeError(f"jp-estat: getStatsData RESULT.STATUS={status!r} "
                           f"({result.get('ERROR_MSG', '')[:120]})")
    data_inf = (body.get("STATISTICAL_DATA") or {}).get("DATA_INF") or {}
    values = _as_list(data_inf.get("VALUE"))
    if not values:
        raise RuntimeError(f"jp-estat: statsDataId={target_id} 数据行为空（上游空窗或口径变更，转人工）")

    redacted_url = url.replace(app_id, "REDACTED")
    scraped_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        value = _num(item.get("$"))
        if value is None:
            continue  # 抑制码/非数值行跳过，绝不造数
        rows.append({
            "stats_data_id": target_id,
            "tab": item.get("@tab"),
            "time": item.get("@time"),
            "cat01": item.get("@cat01"),
            "cat02": item.get("@cat02"),
            "cat03": item.get("@cat03"),
            "area": item.get("@area"),
            "unit": item.get("@unit"),
            "annotation": item.get("@annotation"),
            "value": value,
            "source": SOURCE,
            "source_url": redacted_url,
            "scraped_at": scraped_at,
        })
    if not rows:
        raise RuntimeError(f"jp-estat: statsDataId={target_id} 无可数值化数据行（上游口径变更，转人工）")
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：source secrets 后 python3 spiders/jp-estat/spider.py
    print(json.dumps(run_jp_estat(limit=3), ensure_ascii=False, indent=2))
