"""spiders/amis-market —— AMIS 粮农市场库 G20 商品供需平衡表取数。

数据表面（侦察簿 ★接；本机代理复测 2026-10-07）：

- ``POST https://amis-9189b.appspot.com/fetch``，body ``{"query": "<SQL>"}``，
  **无鉴权、免 key** 直查 BigQuery；响应为 JSON 行数组（SQL 错误时
  HTTP 400 + ``{"detail": "<BigQuery 报错原文>"}``）。
- 与 AMIS 门户前端（app.amis-outlook.org，main bundle
  ``environment.BIGQUERY_ENDPOINT/BIGQUERY_DATASET``）同款调用方式；
  数据在跨项目三段名 ``fao-maps.fao_amis.amis_*``（dataset ``fao_amis``）。
- 供需面 = CBS（Country Balance Sheets，国家平衡表）：6 个商品口径
  （Wheat/Rice/Maize/Soybean + 聚合组 COARSE GRAINS/TOTAL CEREALS）×
  6 个平衡表要素（Production / Imports(NMY) / Exports(NMY) /
  Closing Stocks / Domestic Utilization / Total Utilization）。
  侦察簿称「八商品」，实测 CBS 供需面共 6 个商品口径（含两个聚合组，
  见 README 偏差记录），本单元取全量 6 个。

坑位与容错（实测证据，README 逐条展开）：

- **appspot 域被墙**：必须经代理出口（urllib 缺省尊重
  ``HTTPS_PROXY``/``HTTP_PROXY`` 环境变量；本单元不硬编码代理地址）。
- **服务端单次 50000 行硬上限**（实测恰好 50000 行截断且不报错）：全表
  同条件查询 >50000 行会被静默截尾；本单元固定查询面 + ``max_lastupdate=1``
  过滤后 ~2.5 万行，仍在帽内；**若命中帽即视为取数不可靠，直接失败**。
- **表内同键多修订行**：同一 (product, region, element, year) 键存在多行
  （``last_update`` 不同的历史修订），SQL 固定带 ``max_lastupdate = 1``
  只取最新修订（实测 50000 行含修订 → 过滤后 25450 行、自然键唯一）。
- **三段名必须整体 backtick**（```fao-maps.fao_amis.amis_data` ``）：
  项目段含连字符，不包整体会被 SQL 解析器当作裸标识符拆开报
  ``Syntax error: Expected end of input but got "-"``。
- 上游行不保证排序：SQL 显式 ``ORDER BY product_code, element_code,
  region_name, year`` 后再截 ``limit``（保留尾部 = 最近营销年）。
- ``value`` 为浮点（如 ``760.318808``），归一 round 6 位小数。
- 空响应/非数组/解析后零行 → 失败；**单查询面取数失败即抛
  ``RuntimeError``**（失败即红，不静默返回空列表）。

无工单：机器重启恢复直建（批次二 Wave C），入口函数与行数在 PR body 手工留证。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

SOURCE = "amis-market"
FETCH_URL = "https://amis-9189b.appspot.com/fetch"

# 固定查询面：CBS 供需平衡表 × 6 商品 × 6 要素，只取最新修订（max_lastupdate=1）。
# 三段名整体 backtick（项目段 fao-maps 含连字符）；列名 database/year 不加。
AMIS_SQL = """
SELECT
  d.product_code AS product_code, p.product_name AS product_name,
  d.region_code AS region_code, c.region_name AS region_name,
  d.element_code AS element_code, e.element_name AS element_name,
  d.units AS units, d.year AS year, d.season AS season,
  d.value AS value, d.last_update AS last_update
FROM `fao-maps.fao_amis.amis_data` AS d
INNER JOIN `fao-maps.fao_amis.amis_product` AS p ON d.product_code = p.product_code
INNER JOIN `fao-maps.fao_amis.amis_country` AS c ON d.region_code = c.region_code
INNER JOIN `fao-maps.fao_amis.amis_element` AS e ON d.element_code = e.element_code
WHERE d.database = 'CBS' AND d.max_lastupdate = 1
  AND d.product_code IN (1, 4, 5, 6, 7, 8)
  AND d.element_code IN (5, 7, 10, 16, 20, 35)
  AND d.value IS NOT NULL
ORDER BY d.product_code, d.element_code, c.region_name, d.year
"""

UPSTREAM_ROW_CAP = 50000  # 服务端单响应行硬上限（实测恰好 50000 截断）
TIMEOUT = 90  # 秒；实测带 JOIN 全面 ~17s，超时即失败（不重试、不加频次）

CONTEXT_FIELDS = ("product_code", "region_code", "element_code")


def _post_query(sql: str) -> list:
    """POST 一条 SQL 到 fetch 端点（仅 https 固定主机；无鉴权、无重试放大）。

    代理出口由环境变量 ``HTTPS_PROXY``/``HTTP_PROXY`` 提供（urllib 缺省
    行为），本函数不硬编码代理地址；无代理出口时连接失败 → 抛错。
    """
    req = urllib.request.Request(
        FETCH_URL,
        data=json.dumps({"query": " ".join(sql.split())}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"response is not JSON ({type(exc).__name__})") from None
    if not isinstance(payload, list):
        # SQL 报错时端点回 HTTP 400 + {"detail": ...}（urlopen 先抛 HTTPError）；
        # 兜底：200 但非数组也按失败处理。
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        raise ValueError(f"response is not a row list: {str(detail)[:200]}")
    return payload


def _num(value) -> float | None:
    """value 归一：浮点/字符串 → round 6 位；空/无法解析 → ``None``（缺测）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _match(text, want: str | None) -> bool:
    """客户端精筛：``want`` 为 None 放行全量；否则大小写不敏感精确匹配。"""
    if want is None:
        return True
    return str(text or "").strip().casefold() == str(want).strip().casefold()


def run_amis_market(
    limit: int = 100,
    product: str | None = None,
    element: str | None = None,
    region: str | None = None,
) -> list[dict]:
    """取 AMIS CBS 供需平衡表行；查询失败 → 抛 ``RuntimeError``（失败即红）。

    - 固定查询面（6 商品 × 6 要素 × 各国 + World，2000-2026 营销年）一次取全；
    - ``product``/``element``/``region`` 为客户端精筛（如 ``"Wheat"``/
      ``"Production"``/``"World"``，大小写不敏感精确匹配；缺省不过滤）；
    - 行按 (product, element, region, year) 升序排列，返回最近 ``limit`` 行；
    - golden 重放传 ``limit=100`` + 三参窄化到「Wheat×Production×World」单序列
      （27 行），锚 2017 营销年冻结值，结果恒定。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    try:
        payload = _post_query(AMIS_SQL)
        if len(payload) >= UPSTREAM_ROW_CAP:
            raise RuntimeError(
                f"amis-market: upstream row cap hit ({len(payload)} >= "
                f"{UPSTREAM_ROW_CAP}); query face truncated by server — "
                "refusing unreliable slice"
            )
        if not payload:
            raise RuntimeError("amis-market: query succeeded but returned zero rows")

        now = datetime.now(timezone.utc).isoformat()
        rows: list[dict] = []
        for rec in payload:
            if not isinstance(rec, dict):
                continue
            product_name = str(rec.get("product_name", "")).strip()
            region_name = str(rec.get("region_name", "")).strip()
            element_name = str(rec.get("element_name", "")).strip()
            if not _match(product_name, product) or not _match(element_name, element) \
                    or not _match(region_name, region):
                continue
            value = _num(rec.get("value"))
            if value is None:
                continue
            try:
                year = int(rec.get("year"))
            except (TypeError, ValueError):
                continue
            rows.append({
                "product": product_name,
                "product_code": int(rec.get("product_code")),
                "region": region_name,
                "region_code": int(rec.get("region_code")),
                "element": element_name,
                "element_code": int(rec.get("element_code")),
                "units": str(rec.get("units", "")).strip() or None,
                "year": year,
                "season": str(rec.get("season", "")).strip() or None,
                "value": value,
                "last_update": str(rec.get("last_update", "")).strip() or None,
                "url": FETCH_URL,
                "source": SOURCE,
                "scraped_at": now,
            })
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - 收敛为 RuntimeError（契约：失败即红）
        raise RuntimeError(
            f"amis-market: BigQuery fetch failed — {type(exc).__name__}: {exc}"
        ) from None

    if not rows:
        raise RuntimeError(
            "amis-market: rows parsed but none survived normalization/filters "
            f"(product={product!r}, element={element!r}, region={region!r})"
        )
    return rows[-limit:]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/amis-market/spider.py
    print(json.dumps(run_amis_market(limit=3), ensure_ascii=False, indent=2))
