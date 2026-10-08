"""spiders/jodi-gas —— JODI-Gas 月度天然气供需（world 库，约 94 经济体自报）。

数据表面（确定性两跳通道，免鉴权、直连可达；jodi-oil 兄弟范式）：

1. **发现**：``GET https://api.publisher.jodidata.org/web/files/gas``（JSON，免 key）
   返回当前 publication 的文件清单：``{"publicationId": 27, "files": [{"filename":
   "GAS_world_NewFormat.zip", "format": "CSV", ...}, ...]}``——取 ``format == "CSV"``
   且 ``.zip`` 结尾的条目（``GAS_world_NewFormat.zip``），publicationId 随上游
   重发会漂移，故**不硬编码**、每次经发现接口解析。
2. **下载**：``GET https://www.jodidata.org/jodi-publisher/gas/{publicationId}/{filename}``
   （zip ≈ 1.7 MB，内含单个 SDMX 风格 CSV ≈ 13 MB / 31.6 万行，2009-01..最新月）。
   列与 jodi-oil 完全同构：``REF_AREA,TIME_PERIOD,ENERGY_PRODUCT,FLOW_BREAKDOWN,
   UNIT_MEASURE,OBS_VALUE,ASSESSMENT_CODE``。

数据面（2026-10-08 实测）：94 经济体 × 14 能流 × 单产品 ``NATGAS`` × 三单位
（``TJ`` 能量 / ``M3`` 体积 / ``KTONS`` 质量）；能流：``INDPROD`` 产量 /
``TOTIMPSB`` 总进口（``IMPLNG``/``IMPPIP`` LNG/管道分项）/ ``TOTEXPSB`` 总出口
（``EXPLNG``/``EXPPIP`` 分项）/ ``TOTDEMC``/``TOTDEMO`` 国内总需求（计算/观测，
``STATDIFF`` = 两者差）/ ``STOCKCH`` 库存变化 / ``CLOSTLV`` 期末库存 /
``MAINTOT`` 主要能流合计 / ``OSOURCES`` 其它来源。信号口径 = 经济体×能流的
月度序列（默认滚动最近 12 个月窗口，``year`` 参数可回补任意历史年）。

口坑（jodi-oil 已踩坑逐条继承 + 本源新坑）：

- **下载页不吐直链**：``gas/database/data-downloads.aspx`` 的下载列表是 Vue
  JS 应用（``/assets/index-*.js``）渲染，HTML 里没有 CSV 链接（与 oil 页不同）；
  直链藏在 ``/jodi-publisher/{type}/{publicationId}/{filename}`` 路径后——经
  发现接口拿 publicationId，不逆向 JS、不猜路径。
- **zip 内 CSV 成员名不锚定**：成员名现为 ``STAGING_world_NewFormat.csv``，
  按「``.csv`` 后缀 + 含 ``REF_AREA`` 表头」选成员，不写死文件名。
- **缺测占位防御**：gas world 文件当前无占位（非数值 0 行），但沿用 jodi-oil
  哨兵：``OBS_VALUE`` 非 数值 一律跳过该行（``limit`` 只数真实观测）。
- **404/异常即 HTML 错误页**：按 ``REF_AREA`` 表头嗅探，非 CSV 载荷直接报错。
- **同能流多单位**：同一 ``FLOW_BREAKDOWN`` 在 TJ/M3/KTONS 三单位下各有观测，
  聚合时**绝不可跨单位混加**；本单元原样透出不聚合。
- **经济体加总 ≠ 全球**：覆盖为自报经济体（2009 年仅 16 国，2026 年 94 国），
  早期年覆盖稀疏；下游自行聚合时注意覆盖率。
- **质量旗**：``ASSESSMENT_CODE`` 原样透出为 ``assessment``（上游自评）。
- ``limit`` 语义 = 返回行数上限：全量解析后按（month, country, product, flow,
  unit）字典序确定性排序，保留最近 ``limit`` 行（freshness-first，与 jodi-oil
  的取前 N 不同，README 有论证）；``year`` 固定时重放结果恒定。
- 容错：发现接口/下载/解包/解析任一失败、或过滤后 0 观测行 → 抛
  ``RuntimeError``（失败即红）。

工单：批次三直建（brief 指定 jodi-oil 兄弟范式，先读其 spider/README 再动工）。
"""
from __future__ import annotations

import csv
import io
import json
import urllib.request
import zipfile
from datetime import datetime, timezone

SOURCE = "jodi-gas"
FILES_API = "https://api.publisher.jodidata.org/web/files/gas"
DOWNLOAD_URL_TEMPLATE = (
    "https://www.jodidata.org/jodi-publisher/gas/{publication_id}/{filename}"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/zip, application/json, */*",
}
TIMEOUT = 300  # 秒；world zip ≈ 1.7 MB（解包 13 MB），给足超时
DEFAULT_WINDOW_MONTHS = 12  # year 缺省时的滚动窗口长度


def _get(url: str, expect_json: bool = False):
    """GET 一个固定 https 主机 URL；``expect_json`` 时解析 JSON。失败抛 RuntimeError。"""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001 —— 端点级失败即红
        raise RuntimeError(f"jodi-gas: {url} 取数失败 — {type(exc).__name__}: {exc}") from exc
    if expect_json:
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"jodi-gas: {url} 响应非 JSON — {type(exc).__name__}: {exc}") from exc
    return raw


def discover_csv_url() -> str:
    """经发现接口解析当前 publication 的 CSV zip 直链（publicationId 不硬编码）。"""
    payload = _get(FILES_API, expect_json=True)
    if not isinstance(payload, dict):
        raise RuntimeError(f"jodi-gas: 发现接口响应非 JSON 对象（{type(payload).__name__}）")
    publication_id = payload.get("publicationId")
    files = payload.get("files")
    if not isinstance(publication_id, int) or not isinstance(files, list):
        raise RuntimeError("jodi-gas: 发现接口缺 publicationId/files ——端点形态已变")
    for entry in files:
        if not isinstance(entry, dict):
            continue
        filename = str(entry.get("filename") or "")
        if entry.get("format") == "CSV" and filename.lower().endswith(".zip"):
            return DOWNLOAD_URL_TEMPLATE.format(
                publication_id=publication_id, filename=filename
            )
    raise RuntimeError(f"jodi-gas: 文件清单无 CSV zip 条目（files={files!r:.200}）")


def _load_csv_text() -> tuple[str, str]:
    """下载 zip 并解出 CSV 文本；返回 (csv 文本, 下载 URL)。失败抛 RuntimeError。"""
    url = discover_csv_url()
    raw = _get(url)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            members = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            # 按「.csv 后缀 + REF_AREA 表头」选成员，不锚定成员文件名
            for name in sorted(members):
                with zf.open(name) as fh:
                    text = fh.read().decode("utf-8-sig", "replace")
                if "REF_AREA" in text[:400]:
                    return text, url
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"jodi-gas: 下载载荷非 zip（{url}）— {exc}") from exc
    raise RuntimeError(f"jodi-gas: zip 内无含 REF_AREA 表头的 CSV 成员（{url}）")


def _num(value: str) -> float | None:
    """``OBS_VALUE`` 归一：数值 → float；非数值占位（jodi-oil 同款哨兵）→ ``None``。"""
    text = (value or "").strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def run_jodi_gas(limit: int = 100, year: int | None = None) -> list[dict]:
    """取 JODI-Gas world 库月度供需观测行。

    Args:
        limit: 返回行数上限（确定性排序后保留最近 ``limit`` 行；``year`` 固定时
            重放结果恒定）。
        year: 报告年份过滤（2009 起可回补任意历史年）；缺省 = 数据文件内
            **最近 12 个日历月**的滚动窗口（freshness-first）。

    Returns:
        每行：``year``/``month``/``country``/``flow``/``product``/``value``/
        ``unit``/``assessment`` + ``url``/``source``/``scraped_at``。

    Raises:
        ValueError: ``limit``/``year`` 类型非法。
        RuntimeError: 发现/下载/解包/解析失败或 0 观测行（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []
    if year is not None:
        year = int(year)

    text, url = _load_csv_text()
    rows: list[dict] = []
    max_ym: tuple[int, str] | None = None
    for rec in csv.DictReader(io.StringIO(text)):
        value = _num(rec.get("OBS_VALUE", ""))
        if value is None:  # 缺测占位行不透出（当前 world 文件无占位，防御保留）
            continue
        period = (rec.get("TIME_PERIOD") or "").strip()  # 形如 2009-01
        if "-" not in period:
            continue
        year_s, month_s = period.split("-", 1)
        row_year = int(year_s)
        month = month_s.strip()
        if year is not None and row_year != year:
            continue
        ym = (row_year, month)
        if max_ym is None or ym > max_ym:
            max_ym = ym
        rows.append(
            {
                "year": row_year,
                "month": month,
                "country": (rec.get("REF_AREA") or "").strip(),
                "flow": (rec.get("FLOW_BREAKDOWN") or "").strip(),
                "product": (rec.get("ENERGY_PRODUCT") or "").strip(),
                "value": value,
                "unit": (rec.get("UNIT_MEASURE") or "").strip(),
                "assessment": (rec.get("ASSESSMENT_CODE") or "").strip() or None,
                "url": url,
                "source": SOURCE,
                "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )

    if not rows:
        raise RuntimeError(
            f"jodi-gas: {url} 解析后 0 观测行（year={year!r} 过滤后为空或端点形态已变）"
        )

    if year is None:  # 滚动最近 12 个日历月窗口（按 (year, month) 序）
        wy, wm = max_ym  # type: ignore[unpacking]
        wstart = wy * 12 + (int(wm) - 1) - (DEFAULT_WINDOW_MONTHS - 1)
        rows = [r for r in rows if r["year"] * 12 + (int(r["month"]) - 1) >= wstart]
        if not rows:
            raise RuntimeError("jodi-gas: 滚动窗口过滤后 0 观测行（视为异常）")

    # 排序键含 year：滚动窗口跨日历年，不能像 jodi-oil（单年文件）只按 month 串排序
    rows.sort(key=lambda r: (r["year"], r["month"], r["country"], r["product"], r["flow"], r["unit"]))
    return rows[-limit:]  # freshness-first：保留最近 limit 行


if __name__ == "__main__":  # 冒烟：python3 spiders/jodi-gas/spider.py
    import pprint

    pprint.pp(run_jodi_gas(limit=3))
