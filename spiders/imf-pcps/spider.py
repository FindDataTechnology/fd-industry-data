"""spiders/imf-pcps —— IMF 初级商品价格体系（PCPS）月度序列取数。

数据表面（公开 SDMX 2.1 REST，免 key；工单 brief「侦察簿 W1-A 深潜」）：

- ``GET https://api.imf.org/external/sdmx/2.1/data/PCPS/<key>``
  key = ``COUNTRY.INDICATOR.DATA_TRANSFORMATION.FREQ``（DSD 实测 4 维，顺序如此），
  种子 key ``G001.PCOIL.INDEX.M``（World 原油价格指数，月度；World=G001 非 W00）。
- **只返 XML**（format 参数被忽略）：SDMX-ML StructureSpecificData，``Series``/``Obs``
  元素直接带属性（``TIME_PERIOD`` / ``OBS_VALUE``），用标准库 ``xml.etree`` 按本地名
  解析（不依赖命名空间前缀）。
- 旧 ``dataservices.imf.org`` 已死勿用。

口坑（brief.notes 逐条落实）：

- **错 key 返空数据集不报错**：HTTP 200 + 无 ``Series`` 的空 ``DataSet``——本单元把
  「空序列」计为该 key 失败（留痕），**全部 key 失败抛 ``RuntimeError``**（失败即红，
  绝不静默返回空列表）。
- key 多一段会 400（实测 5 段 key 报 ``has more than expected 4 dimension(s)``）。
- ``limit`` 语义 = 返回行数上限：期次升序排序后保留**最近** ``limit`` 期（生产取最新）；
  golden 重放用小 ``limit`` 锚固定历史 obs，结果恒定。
- OBS_VALUE 为全精度浮点（如 ``68.79415721418565``），归一为 round 6 位小数。
- 免 key、免 UA 伪装；不带任何签名/频次对抗。

工单：``reports/health-tickets/20261006-imf-pcps-fc45b1b5.yaml``（``kind=generate``）
"""
from __future__ import annotations

import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

SOURCE = "imf-pcps"
DATA_URL = "https://api.imf.org/external/sdmx/2.1/data/PCPS"

# 种子序列：World 原油价格指数（月度）。World=G001；四段 key=COUNTRY.INDICATOR.
# DATA_TRANSFORMATION.FREQ；可扩其他商品码（如 G001.PCOIL.USX.M 美元价、PCOM 总指数）。
SEED_SERIES = ("G001.PCOIL.INDEX.M",)
TIMEOUT = 30  # 秒；XML 约 60KB/序列，超时即跳过该序列（不重试、不加频次）


def _get_bytes(url: str) -> bytes:
    """GET 一个端点（仅 https 固定主机；无鉴权、无重试放大）。"""
    req = urllib.request.Request(url, headers={"Accept": "application/xml, */*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        return resp.read()


def _local(tag: str) -> str:
    """元素本地名（对命名空间前缀不敏感：IMF 返回的 Series/Obs 无统一前缀）。"""
    return tag.rsplit("}", 1)[-1]


def _num(value) -> float | None:
    """OBS_VALUE 归一：全精度浮点字符串 → round 6 位；空/无法解析 → ``None``。"""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return round(float(text), 6)
    except ValueError:
        return None


def fetch_series(series_key: str) -> list[dict]:
    """取一条 PCPS 序列的全部 obs；**空数据集抛 ``ValueError``**（错 key 不报错的口）。"""
    url = f"{DATA_URL}/{series_key}"
    try:
        root = ET.fromstring(_get_bytes(url))
    except ET.ParseError as exc:
        raise ValueError(f"series {series_key}: XML parse failed: {exc}") from None

    series_els = [el for el in root.iter() if _local(el.tag) == "Series"]
    if not series_els:
        # IMF 对不存在的 key 返 HTTP 200 + 空 DataSet（无 Series）——必须显式判红
        raise ValueError(f"series {series_key}: empty dataset (no Series; key may be invalid)")

    rows: list[dict] = []
    for ser in series_els:
        ctx = {k: (str(v).strip() or None) for k, v in ser.attrib.items()}
        for obs in ser:
            if _local(obs.tag) != "Obs":
                continue
            period = str(obs.attrib.get("TIME_PERIOD", "")).strip()
            value = _num(obs.attrib.get("OBS_VALUE"))
            if not period:
                continue
            rows.append({
                "period": period,  # SDMX 月期格式，如 2020-M01
                "value": value,    # 指数值（round 6），缺测为 None
                "series_key": series_key,
                "country": ctx.get("COUNTRY"),
                "indicator": ctx.get("INDICATOR"),
                "data_transformation": ctx.get("DATA_TRANSFORMATION"),
                "frequency": ctx.get("FREQUENCY") or ctx.get("FREQ"),
                "url": url,
                "source": SOURCE,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            })
    if not rows:
        raise ValueError(f"series {series_key}: dataset has Series but zero obs")
    rows.sort(key=lambda r: r["period"])
    return rows


def run_imf_pcps(limit: int = 100, series: str | None = None) -> list[dict]:
    """取 PCPS 序列 obs 行；全部序列失败 → 抛 ``RuntimeError``（失败即红）。

    - ``series`` 缺省用种子序列 ``G001.PCOIL.INDEX.M``（可传其他商品码扩展）；
    - 行按期次升序排列，返回最近 ``limit`` 行（生产取最新）；
    - golden 重放传小 ``limit`` 并锚固定历史 obs（2020-M01），结果恒定。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    keys = [series] if series else list(SEED_SERIES)
    rows: list[dict] = []
    errors: list[str] = []
    for key in keys:
        try:
            rows.extend(fetch_series(key))
        except Exception as exc:  # noqa: BLE001 - 单序列失败只跳过并留痕
            errors.append(f"{key}: {type(exc).__name__}: {exc}")
    if not rows:
        raise RuntimeError("imf-pcps: 全部序列取数失败 — " + "; ".join(errors)[:300])
    rows.sort(key=lambda r: r["period"])
    return rows[-limit:]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/imf-pcps/spider.py
    print(json.dumps(run_imf_pcps(limit=3), ensure_ascii=False, indent=2))
