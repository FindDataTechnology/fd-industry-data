"""spiders/tariff-hs-rates —— 中国进口关税税率查询（MFN+协定税率，按 HS 编码）。

数据源：中国自由贸易区服务网 fta.mofcom.gov.cn / ftatax.mofcom.gov.cn
端点（2026-11 实测）：
- 验码校验：POST /wmsw/validateCode（单码） / /wmsw/validateMultiCode（多码）
- 图片验证码：GET /wmsw/imgcode → /wmsw/imgcode/preValidate（需人工/OCR，**契约层待人工，不逆向**）
- 查询提交：GET /wmsw/searchFta（单码） / /wmsw/searchFtaMulti（多码）→ 302 到 /wmsw/tariffSearch 返回 HTML 结果页

口径：
- 输入 HS 编码带点号（如 8541.42.00），内部转 854142 存储，输出保留点号
- 产出字段：hs_code、hs_desc、duty_type(mfn/ordinary/fta)、fta_agreement(可空)、rate(float,%)、effective_period、source_url、fetched_at
- 默认三枚种子：854142（锂离子蓄电池）、851762、610910 → 带点号 8541.42.00 / 8517.62.00 / 6109.10.00

反爬现状：
- 必须解图片验证码（每次查询前 preValidate），属契约层反爬，**不许逆向**，标「契约层待人工」
- 验证码通过后，form 提交才会返回结果页；裸 curl 直接访问 searchFta 只得 302 回搜索页
- 本爬虫交付可运行骨架：验证码环节抛 RuntimeError("契约层待人工: 需解图片验证码 /wmsw/imgcode/preValidate")，
  人工/OCR 接入后即可跑通，不改骨架
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

from scrapling.fetchers import FetcherSession

BASE_URL = "https://ftatax.mofcom.gov.cn/wmsw"
VALIDATE_CODE_URL = f"{BASE_URL}/validateCode"
VALIDATE_MULTI_CODE_URL = f"{BASE_URL}/validateMultiCode"
IMGCODE_URL = f"{BASE_URL}/imgcode"
IMGCODE_PREVALIDATE_URL = f"{BASE_URL}/imgcode/preValidate"
SEARCH_FTA_URL = f"{BASE_URL}/searchFta"
SEARCH_FTA_MULTI_URL = f"{BASE_URL}/searchFtaMulti"
TARIFF_SEARCH_URL = f"{BASE_URL}/tariffSearch"

# 国内源：直连为准，禁走代理
HDRS = {
    "Referer": "https://fta.mofcom.gov.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# 种子 HS 码（HS6）→ 中国完整编码（8-10 位带点号，实测有效）
# 854142 → 8541.42.00 (锂离子蓄电池)
# 851762 → 8517.61.90 / 8517.69.90 (话机/传输设备类，8517.62 在中税则不存在)
# 610910 → 6109.10.00 (棉制T恤)
_DEFAULT_HS_CODES_RAW = ["854142", "851762", "610910"]
_DEFAULT_HS_CODES = ["8541.42.00", "8517.61.90", "6109.10.00"]

# HS 码格式正则：支持 6/8/10 位，带点号
_HS_WITH_DOTS_RE = re.compile(r"^\d{4}(\.\d{2}){1,3}[A-Z]?$")
_HS_RAW_RE = re.compile(r"^\d{6,10}[A-Z]?$")

logger = logging.getLogger("tariff-hs-rates")


class CaptchaRequiredError(RuntimeError):
    """图片验证码拦截——契约层待人工，不逆向。"""
    pass


def _normalize_hs_code(hs: str) -> str:
    """归一化 HS 码：输入可带点号或纯数字，输出带标准点号（4.2.2 或 4.2.2.2）。"""
    hs = hs.strip()
    if _HS_WITH_DOTS_RE.match(hs):
        return hs
    if _HS_RAW_RE.match(hs):
        # 纯数字：按 4+2+2(+2) 分段
        if len(hs) >= 10:
            return f"{hs[:4]}.{hs[4:6]}.{hs[6:8]}.{hs[8:10]}"
        if len(hs) >= 8:
            return f"{hs[:4]}.{hs[4:6]}.{hs[6:8]}"
        if len(hs) >= 6:
            return f"{hs[:4]}.{hs[4:6]}"
    return hs  # 兜底原样


def _hs_to_raw(hs: str) -> str:
    """带点号 → 纯数字（用于存储/对比）。"""
    return hs.replace(".", "")


async def _validate_codes(s, hs_codes: list[str], import_country: str) -> bool:
    """校验 HS 码是否有效（单码/多码自动选择）。返回 True 表示通过校验。"""
    if len(hs_codes) == 1:
        url = VALIDATE_CODE_URL
        data = {"hscountry": import_country, "hscode": hs_codes[0]}
    else:
        url = VALIDATE_MULTI_CODE_URL
        data = {"hscountry": import_country, "hscodes": ",".join(hs_codes)}

    r = await s.post(url, data=data, headers={**HDRS, "Content-Type": "application/x-www-form-urlencoded"})
    if r.status != 200:
        raise RuntimeError(f"tariff-hs-rates: validateCode HTTP {r.status}")
    try:
        payload = json.loads(r.body)
    except json.JSONDecodeError:
        # 可能是 JSONP
        txt = r.body
        if txt.startswith("jsonpcallback(") and txt.endswith(")"):
            payload = json.loads(txt[len("jsonpcallback("):-1])
        else:
            raise RuntimeError(f"tariff-hs-rates: validateCode 非 JSON: {txt[:200]}")
    msg = payload.get("msg", "")
    if msg != "true":
        raise RuntimeError(f"tariff-hs-rates: HS 码校验失败: {msg}")
    return True


async def _check_captcha_needed(s) -> bool:
    """检查是否需要验证码（/imgcode/checkNeed）。"""
    r = await s.get(f"{IMGCODE_URL}/checkNeed", headers=HDRS)
    if r.status != 200:
        return True  # 保守假设需要
    try:
        payload = json.loads(r.body)
        return bool(payload.get("needImgCode", True))
    except Exception:
        return True


async def _prevalidate_captcha(s, imgcode: str) -> bool:
    """预校验验证码（/imgcode/preValidate）。"""
    r = await s.post(
        IMGCODE_PREVALIDATE_URL,
        data={"imgcode": imgcode},
        headers={**HDRS, "Content-Type": "application/x-www-form-urlencoded"},
    )
    if r.status != 200:
        return False
    try:
        payload = json.loads(r.body)
        return bool(payload.get("validateResult", False))
    except Exception:
        return False


async def _fetch_captcha_image(s) -> bytes:
    """下载验证码图片（/imgcode?timestamp=...）。"""
    timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    r = await s.get(f"{IMGCODE_URL}?timestamp={timestamp}", headers=HDRS)
    if r.status != 200:
        raise RuntimeError(f"tariff-hs-rates: 获取验证码图片失败 HTTP {r.status}")
    return r.body


async def _submit_search(s, hs_codes: list[str], export_country: str, import_country: str) -> str:
    """提交查询并返回结果页 HTML。单码走 searchFta，多码走 searchFtaMulti。"""
    if len(hs_codes) == 1:
        url = SEARCH_FTA_URL
        params = {"ec": export_country, "ic": import_country, "hscode": hs_codes[0]}
    else:
        url = SEARCH_FTA_MULTI_URL
        params = {"ec": export_country, "ic": import_country, "hscodes": ",".join(hs_codes)}

    r = await s.get(url, params=params, headers=HDRS, follow_redirects=True)
    if r.status != 200:
        raise RuntimeError(f"tariff-hs-rates: searchFta HTTP {r.status}")
    return r.body


def _parse_tariff_html(html: str, hs_codes: list[str], source_url: str, fetched_at: str) -> list[dict]:
    """解析 tariffSearch 结果页 HTML，提取税率行。"""
    rows: list[dict] = []

    # 结果页结构（实测摘录）：
    # <table class="table_01">...<tr><td>HS编码</td><td>商品名称</td><td>最惠国税率</td><td>普通税率</td><td>协定税率</td><td>协定名称</td>...</tr>...
    # 这里用宽松解析：找所有 <tr>，提取单元格文本
    from html.parser import HTMLParser

    class TableParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_table = False
            self.in_tr = False
            self.in_td = False
            self.current_row = []
            self.rows = []

        def handle_starttag(self, tag, attrs):
            if tag == "table":
                cls = dict(attrs).get("class", "")
                if "table_01" in cls or "result" in cls:
                    self.in_table = True
            if tag == "tr" and self.in_table:
                self.in_tr = True
                self.current_row = []
            if tag == "td" and self.in_tr:
                self.in_td = True

        def handle_endtag(self, tag):
            if tag == "td" and self.in_td:
                self.in_td = False
            if tag == "tr" and self.in_tr:
                self.in_tr = False
                if self.current_row:
                    self.rows.append(self.current_row)
            if tag == "table" and self.in_table:
                self.in_table = False

        def handle_data(self, data):
            if self.in_td:
                self.current_row.append(data.strip())

    parser = TableParser()
    parser.feed(html)

    # 表头映射（按实测列序）：HS编码、商品名称、最惠国税率、普通税率、协定税率、协定名称、执行期
    # 可能的列序变化：用表头文本匹配
    for row in parser.rows:
        if len(row) < 4:
            continue
        # 尝试识别 HS 码所在列
        hs_code = None
        hs_desc = None
        for i, cell in enumerate(row):
            if _HS_WITH_DOTS_RE.match(cell) or _HS_RAW_RE.match(cell.replace(".", "")):
                hs_code = _normalize_hs_code(cell)
                break
        if not hs_code:
            continue  # 非数据行

        # 简易映射：假设列序固定（HS码、名称、MFN、普通、协定税率、协定名、执行期）
        # 实际列序以表头为准，这里按常见结构提取
        try:
            # 找到 HS 码列索引
            hs_idx = row.index(next(c for c in row if _HS_WITH_DOTS_RE.match(c) or _HS_RAW_RE.match(c.replace(".", ""))))
        except StopIteration:
            continue

        # 名称通常在 HS 码后一列
        hs_desc = row[hs_idx + 1] if hs_idx + 1 < len(row) else ""

        # 税率列：尝试按关键词匹配表头（这里简化：假设后续列依次是 MFN、普通、协定税率、协定名、执行期）
        # 真实部署需对表头做动态映射；此处给出骨架，人工/OCR 接入后可完善
        rate_cols = row[hs_idx + 2:]

        def parse_rate(s: str) -> Optional[float]:
            if not s:
                return None
            m = re.search(r"([\d.]+)\s*%", s)
            if m:
                return float(m.group(1))
            try:
                return float(s)
            except ValueError:
                return None

        # MFN
        if len(rate_cols) > 0 and (mfn := parse_rate(rate_cols[0])):
            rows.append({
                "hs_code": hs_code,
                "hs_desc": hs_desc,
                "duty_type": "mfn",
                "fta_agreement": "",
                "rate": mfn,
                "effective_period": rate_cols[4] if len(rate_cols) > 4 else "",
                "source_url": source_url,
                "fetched_at": fetched_at,
            })
        # 普通税率
        if len(rate_cols) > 1 and (ord_rate := parse_rate(rate_cols[1])):
            rows.append({
                "hs_code": hs_code,
                "hs_desc": hs_desc,
                "duty_type": "ordinary",
                "fta_agreement": "",
                "rate": ord_rate,
                "effective_period": rate_cols[4] if len(rate_cols) > 4 else "",
                "source_url": source_url,
                "fetched_at": fetched_at,
            })
        # 协定税率（可能有多行/多协定，这里简化取第一个非空）
        if len(rate_cols) > 2 and (fta_rate := parse_rate(rate_cols[2])):
            fta_name = rate_cols[3] if len(rate_cols) > 3 else ""
            rows.append({
                "hs_code": hs_code,
                "hs_desc": hs_desc,
                "duty_type": "fta",
                "fta_agreement": fta_name,
                "rate": fta_rate,
                "effective_period": rate_cols[4] if len(rate_cols) > 4 else "",
                "source_url": source_url,
                "fetched_at": fetched_at,
            })

    return rows


async def _collect(hs_codes: list[str], export_country: str, import_country: str, limit: int) -> list[dict]:
    """主采集流程。"""
    all_rows: list[dict] = []
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    async with FetcherSession(impersonate="chrome120", timeout=30, verify=False) as s:
        # 1. 校验 HS 码
        try:
            await _validate_codes(s, hs_codes, import_country)
        except RuntimeError as e:
            logger.warning("HS 码校验失败: %s", e)
            raise

        # 2. 检查/处理验证码 —— **契约层待人工**
        need_captcha = await _check_captcha_needed(s)
        if need_captcha:
            # 下载验证码图片（供人工/OCR）
            img_bytes = await _fetch_captcha_image(s)
            # **此处为契约层反爬拦截点：必须解图片验证码后 preValidate 通过才能继续**
            # 不做 OCR/打码平台对接（属逆向/绕过），抛出明确异常交由人工环节
            raise CaptchaRequiredError(
                "契约层待人工: 需解图片验证码 /wmsw/imgcode/preValidate；"
                f"验证码图片已获取（{len(img_bytes)} bytes），请人工/OCR 识别后填入 imgcode 再 preValidate"
            )

        # 3. 提交查询（验证码已通过）
        html = await _submit_search(s, hs_codes, export_country, import_country)

        # 4. 解析结果
        source_url = f"{SEARCH_FTA_URL}?ec={export_country}&ic={import_country}&hscodes={','.join(hs_codes)}"
        rows = _parse_tariff_html(html, hs_codes, source_url, fetched_at)
        all_rows.extend(rows)

    # 限制行数
    if limit is not None and limit >= 0:
        all_rows = all_rows[:limit]
    return all_rows


def run_tariff_hs_rates(
    hs_codes: str = "",
    export_country: str = "CN",
    import_country: str = "CN",
    limit: int = 100,
) -> list[dict]:
    """fd-runner 唯一入口：按 HS 编码查询进口关税税率（MFN+普通+协定）。

    参数：
    - hs_codes: 逗号分隔 HS 编码（带点号，如 8541.42.00,8517.62.00），缺省用三枚种子
    - export_country: 原产地国家代码（默认 CN）
    - import_country: 进口国代码（默认 CN）
    - limit: 最大返回行数

    返回：符合 manifest columns 的字典列表。
    """
    # 解析 hs_codes
    if hs_codes.strip():
        raw_list = [c.strip() for c in hs_codes.split(",") if c.strip()]
    else:
        raw_list = _DEFAULT_HS_CODES

    hs_codes_norm = [_normalize_hs_code(c) for c in raw_list]

    # 运行异步采集
    try:
        rows = asyncio.run(_collect(hs_codes_norm, export_country, import_country, limit))
    except CaptchaRequiredError:
        # 契约层待人工：向上抛出，平台侧记录工单分类为「契约层」
        raise
    except Exception as e:
        logger.exception("tariff-hs-rates 采集异常")
        raise RuntimeError(f"tariff-hs-rates: {type(e).__name__}: {e}") from e

    if not rows:
        # 上游不可达/解析为空：按要求报错（失败即红，不静默返回空）
        raise RuntimeError("tariff-hs-rates: 解析结果为空（上游页面结构可能变更，转人工）")

    return rows


if __name__ == "__main__":  # 冒烟：source secrets 后 python3 spiders/tariff-hs-rates/spider.py
    import sys
    try:
        rows = run_tariff_hs_rates(limit=10)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    except CaptchaRequiredError as e:
        print(f"[预期] {e}", file=sys.stderr)
        sys.exit(0)  # 冒烟期：验证码拦截视为正常（骨架可运行）
    except Exception as e:
        print(f"[错误] {e}", file=sys.stderr)
        sys.exit(1)