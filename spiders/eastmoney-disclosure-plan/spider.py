"""spiders/eastmoney-disclosure-plan —— 东方财富定期报告预约披露日历（42 家 A 股银行）。

用途：银行定期报告「到期未披露」预警数据源（C 预警线 / B 财报线的巡检输入）。

数据表面（东财数据中心公开 JSON，**免 key、免鉴权、常规 UA 直连**，2026-11-07 实测）：

    GET https://datacenter-web.eastmoney.com/api/data/v1/get
        ?reportName=RPT_PUBLIC_BS_APPOIN
        &columns=ALL&pageSize=500&pageNumber=1
        &sortColumns=SECURITY_CODE&sortTypes=1
        &filter=<整体 urlencode 后的表达式>

关键坑（README 有完整证据）：``filter`` 参数**必须整体 urlencode**
（``urllib.parse.urlencode({"filter": expr})`` 或整个 query 一起 urlencode）——
只编码空格/引号等部分字符会返回非 JSON 响应或连接重置。银行筛选走 ``in``
批量表达式 ``(SECURITY_CODE in ("601398",...))``；超过一页时按响应
``result.pages`` 逐页翻（pageSize=500，2007 至今全史 2,080 行为 5 页）。

字段（以实测响应为准，``columns=ALL`` 返回 29 列，本单元只取 8 个上游字段）：

- ``SECURITY_CODE`` 股票代码 / ``SECURITY_NAME_ABBR`` 简称
- ``REPORT_TYPE`` 报告类型：**1=一季报 2=半年报 3=三季报 4=年报**
- ``REPORT_YEAR`` 报告年度（fiscal year）
- ``FIRST_APPOINT_DATE`` 首次预约披露日
- ``FIRST_CHANGE_DATE`` / ``SECOND_CHANGE_DATE`` / ``THIRD_CHANGE_DATE`` 变更日
  （逐次后移；取其中**最新**非空者为 ``change_date``）
- ``APPOINT_CHANGE`` 变更方向（提前/延后，可空）
- ``ACTUAL_PUBLISH_DATE`` 实际披露日（可空——未披露即为 null）

行设计（10 键，与 manifest columns 一一对应）：
``stock_code`` / ``stock_name`` / ``report_type``（年报/半年报/一季报/三季报）/
``fiscal_year``（int）/ ``first_appoint_date`` / ``change_date``（可空）/
``actual_publish_date``（可空保留 null）/ ``plan_status``（预约中/已变更/已披露，
本地派生）/ ``source``（常量）/ ``scraped_at``。

窗口：上游数据自 **2007-03** 起（最早 FIRST_APPOINT_DATE=2007-03-14）；首爬
默认窗口 ``FIRST_APPOINT_DATE >= 2025-01-01``（336 行，2026-11-07 实测），
若 ``limit`` 超过窗口内行数则自动放宽到 2007 全史——``limit`` 真实生效。

失败即红：HTTP 错误 / 非 JSON / ``success != true`` / 缺结构 / 缺字段 /
未知 REPORT_TYPE / 0 观测行 → ``RuntimeError``，绝不静默回空。
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

def _load_universes_from_repo():
    """从 checkout 的 fd_industry_data/universes.py 加载模块（镜像旧包兜底）。"""
    import importlib.util as _ilu
    import sys as _sys
    from pathlib import Path as _Path

    _mod_path = _Path(__file__).resolve().parents[2] / "fd_industry_data" / "universes.py"
    if not _mod_path.is_file():
        raise FileNotFoundError(_mod_path)
    _spec = _ilu.spec_from_file_location("fd_industry_data.universes", _mod_path)
    _mod = _ilu.module_from_spec(_spec)
    _sys.modules["fd_industry_data.universes"] = _mod
    _spec.loader.exec_module(_mod)
    return _mod


try:
    from fd_industry_data import universes
except ImportError:
    # 镜像未安装该包 —— 从 checkout 加载
    universes = _load_universes_from_repo()
else:
    # 镜像已安装，但可能是**旧版**（无 cwd 兜底），registry_root() 会抛 UniverseRegistryError
    # 实测 fd-industry-runner:sha-e6f5f01：universes/ 只在 /app，pod workingDir=/content
    # 旧版只从 __file__ 向上找，永远命不中 → 四次 UniverseRegistryError。
    # 单元侧自愈：捕获 UniverseRegistryError 并回退到 checkout 副本。
    try:
        universes.registry_root()
    except universes.UniverseRegistryError:
        universes = _load_universes_from_repo()

SOURCE = "eastmoney-disclosure-plan"

BASE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_PUBLIC_BS_APPOIN"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
}

TIMEOUT = 60          # 秒/请求；单请求单尝试，不重试
PAGE_SIZE = 500       # 上游单页上限（实测 pageSize=500 生效）
LIMIT_CAP = 2080      # limit 上限 = 2007 至今全史行数（42 家 × ~13 年 × 4 期）

# 首爬窗口与可回溯深度（均为 FIRST_APPOINT_DATE 口径，见 README §3）
FIRST_CRAWL_WINDOW_START = "2025-01-01"
HISTORY_START = "2007-01-01"

REPORT_TYPE_NAMES = {
    "1": "一季报",
    "2": "半年报",
    "3": "三季报",
    "4": "年报",
}

# 默认宇宙来自注册表 universes/cn-a.yaml（vertical-lane-generalization）。
# 本单元 MUST NOT 硬编码公司列表：银行清单在注册表里以 ``verticals: [bank]``
# 登记，本单元按 DEFAULT_VERTICALS 过滤取用。
#
# 为什么默认仍是银行而不是整个 CN-A 注册表：本单元是**在产**日频单元
# （manifest schedule "32 12 * * *"），默认宇宙变化会直接改变生产输出。酒店/
# 餐饮等其它垂直线以 ``codes`` 参数显式传入，不并入默认值。
DEFAULT_REGION = "CN-A"
DEFAULT_VERTICALS = ("bank",)

# 上游必需字段（缺任一即字段形态已变 → 失败即红）
REQUIRED_FIELDS = (
    "SECURITY_CODE",
    "SECURITY_NAME_ABBR",
    "REPORT_TYPE",
    "REPORT_YEAR",
    "FIRST_APPOINT_DATE",
)
_CHANGE_FIELDS = ("FIRST_CHANGE_DATE", "SECOND_CHANGE_DATE", "THIRD_CHANGE_DATE")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_YEAR_RE = re.compile(r"^\d{4}$")


def _request_json(url: str) -> dict:
    """GET 并解析 JSON；网络/解码/解析失败统一抛 ``RuntimeError``。"""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 官方主机)
            body = resp.read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001 —— 单请求单尝试，端点级失败即红
        raise RuntimeError(
            f"{SOURCE}: 请求失败 {url} — {type(exc).__name__}: {exc}"
        ) from exc
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            f"{SOURCE}: 响应非 JSON（{type(exc).__name__}: {exc}）——"
            "filter 未整体 urlencode 是最常见原因（见 README §4 头号坑）"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"{SOURCE}: 响应顶层非 JSON 对象（{type(payload).__name__}）——端点形态已变"
        )
    return payload


def _default_codes() -> tuple[str, ...]:
    """注册表驱动的默认宇宙：CN-A 中 ``verticals`` 命中 DEFAULT_VERTICALS 者。

    注册表不可用时硬失败 —— 不静默回落到内置清单。
    """
    wanted = set(DEFAULT_VERTICALS)
    codes = tuple(
        e.ticker for e in universes.load_region(DEFAULT_REGION) if wanted & set(e.verticals)
    )
    if not codes:
        raise RuntimeError(
            f"{SOURCE}: 注册表 {DEFAULT_REGION} 无 verticals={sorted(wanted)} 的实体"
        )
    return codes


def _normalize_codes(codes) -> tuple[str, ...]:
    """接受 None（注册表默认）/ 列表 / 逗号分隔串；空集合是硬失败。"""
    if codes is None:
        return _default_codes()
    if isinstance(codes, str):
        parts = [p.strip() for p in codes.split(",")]
    elif isinstance(codes, (list, tuple, set)):
        parts = [str(p).strip() for p in codes]
    else:
        raise ValueError(f"{SOURCE}: codes 必须是列表或逗号分隔字符串，得到 {type(codes).__name__}")
    out: list[str] = []
    seen: set[str] = set()
    for code in parts:
        if not code:
            continue
        if not (code.isdigit() and len(code) == 6):
            raise ValueError(f"{SOURCE}: 股票代码 {code!r} 不是 6 位数字")
        if code in seen:
            continue
        seen.add(code)
        out.append(code)
    if not out:
        raise ValueError(f"{SOURCE}: codes 为空集合——不静默回落到注册表默认宇宙")
    return tuple(out)


def _build_filter(since: str, codes) -> str:
    """构造东财 filter 表达式：宇宙 ``in`` 批量 + 预约日下界。

    条件之间直接**并列括号拼接**（``(...)(...)``），这是东财 filter 的语法；
    实测用 ``and`` 连接会静默返回 0 行（见 README §4）。返回值必须由调用方
    **整体 urlencode**，不得逐段拼接编码。
    """
    joined = ",".join(f'"{code}"' for code in codes)
    return f"(SECURITY_CODE in ({joined}))(FIRST_APPOINT_DATE>='{since}')"


def _build_url(filter_expr: str, page_number: int) -> str:
    """整条 query 一起 urlencode——filter 表达式被整体编码（头号坑）。"""
    query = {
        "reportName": REPORT_NAME,
        "columns": "ALL",
        "pageSize": str(PAGE_SIZE),
        "pageNumber": str(page_number),
        "sortColumns": "SECURITY_CODE",
        "sortTypes": "1",
        "filter": filter_expr,
    }
    return f"{BASE_URL}?{urllib.parse.urlencode(query)}"


def _iso_date(value: object, field: str) -> str | None:
    """``"2026-03-31 00:00:00"`` → ``"2026-03-31"``；空值→None。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text = str(value).strip()
    if not _DATE_RE.match(text):
        raise RuntimeError(
            f"{SOURCE}: {field} 非 YYYY-MM-DD 形态（{value!r}）——字段形态已变"
        )
    return text[:10]


def _row_from_item(item: dict, scraped_at: str) -> dict:
    """把一条上游记录映射为输出行；未知报告类型/缺必需字段即抛错。"""
    missing = [key for key in REQUIRED_FIELDS if key not in item]
    if missing:
        raise RuntimeError(
            f"{SOURCE}: 记录缺字段 {missing}（实际键 {sorted(item)}）——字段形态已变"
        )

    report_type_raw = str(item["REPORT_TYPE"]).strip()
    report_type = REPORT_TYPE_NAMES.get(report_type_raw)
    if report_type is None:
        raise RuntimeError(
            f"{SOURCE}: 未知 REPORT_TYPE {report_type_raw!r}"
            f"（期望 {sorted(REPORT_TYPE_NAMES)}）——上游新增报告类型，需人工映射"
        )

    fiscal_year_raw = str(item["REPORT_YEAR"]).strip()
    if not _YEAR_RE.match(fiscal_year_raw):
        raise RuntimeError(
            f"{SOURCE}: REPORT_YEAR 非 4 位年份（{item['REPORT_YEAR']!r}）"
            "——字段形态已变"
        )
    fiscal_year = int(fiscal_year_raw)

    first_appoint = _iso_date(item.get("FIRST_APPOINT_DATE"), "FIRST_APPOINT_DATE")
    if first_appoint is None:
        raise RuntimeError(
            f"{SOURCE}: 记录 FIRST_APPOINT_DATE 为空"
            f"（{item.get('SECURITY_CODE')!r} {fiscal_year_raw} {report_type}）"
            "——必填输出字段不可缺"
        )

    change_dates = [
        d for d in (_iso_date(item.get(f), f) for f in _CHANGE_FIELDS) if d
    ]
    change_date = max(change_dates) if change_dates else None
    actual = _iso_date(item.get("ACTUAL_PUBLISH_DATE"), "ACTUAL_PUBLISH_DATE")

    # plan_status 本地派生：已披露 > 已变更（有变更日或变更方向标记）> 预约中
    if actual:
        plan_status = "已披露"
    elif change_date or item.get("APPOINT_CHANGE"):
        plan_status = "已变更"
    else:
        plan_status = "预约中"

    return {
        "stock_code": str(item["SECURITY_CODE"]).strip(),
        "stock_name": str(item["SECURITY_NAME_ABBR"]).strip(),
        "report_type": report_type,
        "fiscal_year": fiscal_year,
        "first_appoint_date": first_appoint,
        "change_date": change_date,
        "actual_publish_date": actual,
        "plan_status": plan_status,
        "source": SOURCE,
        "scraped_at": scraped_at,
    }


def _fetch_rows(since: str, scraped_at: str, codes) -> list[dict]:
    """按窗口抓全部页（``result.pages`` 驱动），任一页异常即整体失败。"""
    filter_expr = _build_filter(since, codes)
    rows: list[dict] = []
    page = 1
    pages = 1
    count = 0
    while page <= pages:
        url = _build_url(filter_expr, page)
        payload = _request_json(url)
        if payload.get("success") is not True or payload.get("code") != 0:
            raise RuntimeError(
                f"{SOURCE}: 上游返回失败（success={payload.get('success')!r}, "
                f"code={payload.get('code')!r}, message={payload.get('message')!r}）"
                f"— {url}"
            )
        result = payload.get("result")
        if result is None:  # 0 结果时 result=null（如表达式语法不被识别）
            break
        if not isinstance(result, dict):
            raise RuntimeError(
                f"{SOURCE}: result 非 JSON 对象（{type(result).__name__}）——端点形态已变"
            )
        data = result.get("data")
        if data is None:
            data = []
        if not isinstance(data, list):
            raise RuntimeError(
                f"{SOURCE}: result.data 非 JSON 数组（{type(data).__name__}）——端点形态已变"
            )
        try:
            pages = int(result.get("pages") or 0)
            count = int(result.get("count") or 0)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"{SOURCE}: result.pages/count 非整数（{result.get('pages')!r}/"
                f"{result.get('count')!r}）——端点形态已变"
            ) from exc
        for item in data:
            if not isinstance(item, dict):
                continue
            rows.append(_row_from_item(item, scraped_at))
        page += 1

    if page > 1 and count and len(rows) != count:
        raise RuntimeError(
            f"{SOURCE}: 分页行数不一致（count={count}, 实取={len(rows)}）"
            "——上游翻页期间发生变更，重跑即可；不静默返回残缺数据"
        )
    return rows


def _order_and_limit(rows: list[dict], limit: int) -> list[dict]:
    """按 (stock_code, report_type) 去重，first_appoint_date 倒序，截取 limit。"""
    dedup: dict[tuple, dict] = {}
    for row in rows:
        key = (row["stock_code"], row["fiscal_year"], row["report_type"])
        dedup[key] = row  # 上游滚动修订取新（同键重复时后写覆盖）
    ordered = sorted(
        dedup.values(), key=lambda r: (r["stock_code"], r["report_type"])
    )
    ordered.sort(key=lambda r: r["first_appoint_date"], reverse=True)
    return ordered[:limit]


def run_eastmoney_disclosure_plan(limit: int = 200, codes=None) -> list[dict]:
    """取 A 股定期报告预约披露日历（最近预约日优先）。

    Args:
        limit: 返回行数上限（默认真实生效）。首爬窗口为
            ``FIRST_APPOINT_DATE >= 2025-01-01``；若该窗口行数不足 ``limit``，
            自动放宽到 2007 全史（上游最早 2007-03-14）。
        codes: 监控宇宙（6 位股票代码列表或逗号分隔串）。缺省 = 注册表
            ``universes/cn-a.yaml`` 中 ``verticals`` 命中 ``DEFAULT_VERTICALS``
            的实体（即 42 家银行）。其它垂直线（酒店/餐饮）显式传入自己的
            A 股宇宙；传空集合是硬失败，不静默回落默认宇宙。

    Returns:
        每行 10 键：``stock_code`` / ``stock_name`` / ``report_type``
        （年报/半年报/一季报/三季报）/ ``fiscal_year``（int）/
        ``first_appoint_date``（YYYY-MM-DD）/ ``change_date``（YYYY-MM-DD 或
        None）/ ``actual_publish_date``（YYYY-MM-DD 或 None）/
        ``plan_status``（预约中/已变更/已披露）/ ``source``（常量
        ``eastmoney-disclosure-plan``）/ ``scraped_at``（ISO 时间戳）。
        按 ``first_appoint_date`` 倒序（同日按代码、报告类型升序）确定性排序。

    Raises:
        ValueError: ``limit`` 类型非法。
        RuntimeError: HTTP 错误 / 非 JSON / success!=true / 缺结构 / 缺字段 /
            未知报告类型 / 分页不一致 / 0 观测行——失败即红，绝不静默回空。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    limit = max(1, min(limit, LIMIT_CAP))

    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    universe = _normalize_codes(codes)
    rows = _fetch_rows(FIRST_CRAWL_WINDOW_START, scraped_at, universe)
    if len(rows) < limit and FIRST_CRAWL_WINDOW_START != HISTORY_START:
        # limit 超过首爬窗口：放宽到 2007 全史，保证 limit 真实生效
        rows = _fetch_rows(HISTORY_START, scraped_at, universe)

    if not rows:
        raise RuntimeError(
            f"{SOURCE}: 解析后 0 观测行（窗口 FIRST_APPOINT_DATE >= "
            f"{FIRST_CRAWL_WINDOW_START}）——端点形态已变或上游停更"
        )
    return _order_and_limit(rows, limit)


if __name__ == "__main__":  # 冒烟：.venv/bin/python spiders/eastmoney-disclosure-plan/spider.py
    import pprint

    pprint.pp(run_eastmoney_disclosure_plan(limit=5))
