"""spiders/sac-securities —— 中国证券业协会（SAC）证券公司经营数据（行业汇总，累计口径）。

数据表面（SAC 官网「信息公开 > 行业数据 > 证券公司经营数据」栏目，
**免 key、免鉴权、直连可达**）：

- 列表页 ``GET https://www.sac.net.cn/xxgk/hysj/jjsj/``（HTML；第 1 页 25 篇，
  更早各期在 index_1.html，本单元只取第 1 页）。每条 = 文章链接 + 标题
  （如「中国证券业协会发布证券公司2026年上半年度经营数据」）+ 发布日期。
- 文章页正文是 TRS CMS 的 HTML **散文段落**（无表格、无 JSON 接口、近年无 xls/pdf
  附件），全部指标内嵌在句子里：营业收入及各主营业务收入（代理买卖证券业务净收入、
  证券承销与保荐业务净收入、财务顾问业务净收入、投资咨询业务净收入、资产管理业务
  净收入、利息净收入、证券投资收益）、净利润；总资产、净资产、净资本、客户交易
  结算资金余额、受托管理资金本金总额（旧期另有 托管证券市值）。
- 行粒度 = 每期每指标一行；口径 = 当期全部证券公司合计（150 家等家数随期变化，
  不透出）→ ``company_scope`` 常量 ``行业汇总``。

通道选择（JSON 优先）：SAC 官网为 TRS CMS，无任何 JSON 端点；数据仅以散文段落
呈现，故唯一可行通道 = 解析文章 HTML 散文（标准库 ``re``，无新依赖）。实测列表
第 1 页覆盖 2018Q1..2026H1 共 25 期，每期约 14 个指标行。

口径约定（README 详述）：

- ``stat_period`` = ``YYYY-Qn`` 累计口径：年度→Q4、上半年度→Q2、前三季度/三季度→Q3、
  一季度→Q1（上游数值均为年初至报告期末的累计数）。
- 金额统一折算为 **亿元**：正文按「万亿元」计的资产负债类指标 ×10000。上游计量
  单位随期漂移（如客户交易结算资金余额 2018Q3 为亿元、2019 起为万亿元），解析时
  捕获数字后的计量单位再归一，不硬编码。
- ``indicator`` 剥离括号限定语（「（含席位租赁）」/「（含交易单元席位租赁）」随期
  不同，剥离后跨期可 join）；官方全名码表见 README。

坑位（README 详述）：官网错误路径返回 nginx 404（brief 给的 /hgsjbgz/ 已失效）；
正文「X家公司实现盈利」不得误配进金额；2018 年措辞为「当期实现营业收入」（仍为
累计口径）；SAC 对频次敏感——单次运行只取列表页 1 次 + 为满足 ``limit`` 所需的
少量文章页（上限 ``MAX_ARTICLES``），不做批量爬取。

容错：HTTP 错误 / 列表页无文章 / 单篇文章 0 指标行 / 总 0 行 → 抛
``RuntimeError``（失败即红，绝不静默回空）。
"""
from __future__ import annotations

import html as html_lib
import re
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urljoin

SOURCE = "sac-securities"
LIST_URL = "https://www.sac.net.cn/xxgk/hysj/jjsj/"  # 证券公司经营数据栏目列表页（第 1 页）

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html, */*",
}
TIMEOUT = 30  # 秒/请求；单端点单请求不重试（SAC 对频次敏感，遇失败即红）

UNIT = "亿元"        # 全部金额统一折算为亿元后的单位码（本单元常量）
SCOPE = "行业汇总"   # 行业合计口径（公司家数随期变化，不透出）
MAX_ARTICLES = 8     # 单次运行最多取回的文章页数（每篇约 14 行，足够覆盖合理 limit）

# 列表页条目：<li><a href="./202609/t20260903_82250.html" ...><img .../>
#              <span>标题</span><span class="time">2026-09-04</span></a></li>
_LIST_ITEM_RE = re.compile(
    r'<a[^>]+href="((?:\./)?\d{6}/t\d+_\d+\.html)"[^>]*>.*?'
    r'<span>([^<]+)</span>\s*<span class="time">(\d{4}-\d{2}-\d{2})</span>',
    re.S,
)

# 正文指标模式：数字（可含千分位逗号）+ 计量单位（亿元/万亿元，随期捕获）。
# 括号限定语全角/半角均可、可有可无（2019「含席位租赁」vs 2025「含交易单元席位租赁」）。
_NUM = r"(\d[\d,，]*(?:\.\d+)?)"
_PAREN = r"(?:（[^（）]*）|\([^()]*\))?"
_UNIT = "(亿元|万亿元)"

# (归一后的指标名, 正则)；锚定短语在 2018/2019/2020/2025/2026 五期实测文中逐一验证
INDICATOR_PATTERNS: tuple[tuple[str, str], ...] = (
    ("营业收入", "实现营业收入" + _NUM + _UNIT),
    ("净利润", "实现净利润" + _NUM + _UNIT),
    ("代理买卖证券业务净收入", "代理买卖证券业务净收入" + _PAREN + _NUM + _UNIT),
    ("证券承销与保荐业务净收入", "证券承销与保荐业务净收入" + _PAREN + _NUM + _UNIT),
    ("财务顾问业务净收入", "财务顾问业务净收入" + _PAREN + _NUM + _UNIT),
    ("投资咨询业务净收入", "投资咨询业务净收入" + _PAREN + _NUM + _UNIT),
    ("资产管理业务净收入", "资产管理业务净收入" + _PAREN + _NUM + _UNIT),
    ("利息净收入", "利息净收入" + _PAREN + _NUM + _UNIT),
    ("证券投资收益", "证券投资收益" + _PAREN + _NUM + _UNIT),
    ("总资产", "总资产为" + _NUM + _UNIT),
    ("净资产", "净资产为" + _NUM + _UNIT),
    ("净资本", "净资本为" + _NUM + _UNIT),
    ("客户交易结算资金余额", "客户交易结算资金余额" + _PAREN + _NUM + _UNIT),
    ("托管证券市值", "托管证券市值" + _NUM + _UNIT),
    ("受托管理资金本金总额", "受托管理资金本金总额" + _NUM + _UNIT),
)


def _fetch(url: str) -> str:
    """取一个 SAC 页面（utf-8 为主，兼容 gb18030）；失败抛 ``RuntimeError``。"""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001 —— 端点级失败即红
        raise RuntimeError(f"sac-securities: {url} 取数失败 — {type(exc).__name__}: {exc}") from exc
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gb18030", errors="replace")


def _parse_articles(listing: str) -> list[tuple[str, str, str]]:
    """列表页 → [(文章 URL, 标题, 发布日期 YYYY-MM-DD)]，保持页面顺（新→旧）。"""
    out: list[tuple[str, str, str]] = []
    for href, title, pub in _LIST_ITEM_RE.findall(listing):
        title = html_lib.unescape(title).strip()
        if "经营数据" not in title:
            continue
        out.append((urljoin(LIST_URL, href), title, pub))
    return out


def _period_from_title(title: str) -> str | None:
    """标题 → ``YYYY-Qn`` 累计口径期号；非经营数据文章/无年份 → None。

    顺序敏感：「上半年度」含「年度」，必须先判半年度再判年度。
    """
    if "经营数据" not in title:
        return None
    m = re.search(r"(\d{4})年", title)
    if not m:
        return None
    year = m.group(1)
    if re.search(r"上半年度|上半年|半年度", title):
        q = "Q2"
    elif re.search(r"前三季度|第三季度|三季度", title):
        q = "Q3"
    elif re.search(r"一季度|第一季度", title):
        q = "Q1"
    elif re.search(r"年度", title):
        q = "Q4"
    else:
        return None
    return f"{year}-{q}"


def _article_text(page: str) -> str:
    """文章页 → 去标签散文纯文本（剥 script/style、去全部空白含全角空格）。"""
    i = page.find('id="zoom"')
    if i >= 0:
        page = page[i:]  # 正文容器，跳过导航/菜单
    j = page.find('<div class="top_fole"')
    if j >= 0:
        page = page[:j]  # 截到「附件下载」区之前
    page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    text = html_lib.unescape(re.sub(r"<[^>]+>", "", page))
    return re.sub(r"[\s\u3000]+", "", text)


def _to_yi(num: str, unit: str) -> float:
    """数值归一为亿元（剥离千分位逗号；万亿元 ×10000）。"""
    value = float(num.replace(",", "").replace("，", ""))
    if unit == "万亿元":
        value *= 10000.0
    return value


def _extract_rows(text: str, period: str, url: str, pub: str, scraped_at: str) -> list[dict]:
    """散文文本 → 该期指标行（每指标一行；未匹配的指标跳过）。"""
    rows: list[dict] = []
    for indicator, pattern in INDICATOR_PATTERNS:
        m = re.search(pattern, text)
        if not m:
            continue
        rows.append(
            {
                "source": SOURCE,
                "stat_period": period,
                "indicator": indicator,
                "value": _to_yi(m.group(1), m.group(2)),
                "unit": UNIT,
                "company_scope": SCOPE,
                "source_url": url,
                "upstream_updated": pub,
                "scraped_at": scraped_at,
            }
        )
    return rows


def run_sac_securities(limit: int = 50) -> list[dict]:
    """取 SAC 证券公司经营数据（行业汇总，期粒度 ``YYYY-Qn`` 累计口径）。

    Args:
        limit: 返回行数上限；全量解析后按（stat_period, indicator）字典序确定性
            排序，保留最近 ``limit`` 行（生产取最新期次；固定参数重放结果恒定）。
            单次运行最多取 ``MAX_ARTICLES`` 篇文章页（约百余行），``limit`` 更大时
            以实际取到的行为准。

    Returns:
        每行：``source``（常量 ``sac-securities``）/ ``stat_period``（``YYYY-Qn``，
        累计口径）/ ``indicator``（指标名，括号限定语已剥离）/ ``value``（float，
        统一亿元）/ ``unit``（常量 ``亿元``）/ ``company_scope``（常量
        ``行业汇总``）/ ``source_url``（文章页 URL）/ ``upstream_updated``
        （上游发布日期 YYYY-MM-DD）/ ``scraped_at``（ISO 时间戳）。

    Raises:
        ValueError: ``limit`` 类型非法。
        RuntimeError: 端点不可达、列表无文章、单篇 0 指标行或总 0 观测行（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    limit = max(1, min(limit, 500))
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    listing = _fetch(LIST_URL)
    articles = _parse_articles(listing)
    if not articles:
        raise RuntimeError(
            f"sac-securities: {LIST_URL} 解析后 0 篇经营数据文章——入口/版式已变"
        )

    rows: list[dict] = []
    fetched = 0
    for url, title, pub in articles:  # 页面顺即新→旧
        if len(rows) >= limit or fetched >= MAX_ARTICLES:
            break
        period = _period_from_title(title)
        if period is None:
            continue
        text = _article_text(_fetch(url))  # 取数失败直接 RuntimeError
        got = _extract_rows(text, period, url, pub, scraped_at)
        if not got:
            raise RuntimeError(
                f"sac-securities: {url}（{title}）解析后 0 指标行——正文版式已变"
            )
        rows.extend(got)
        fetched += 1

    if not rows:
        raise RuntimeError(
            f"sac-securities: {LIST_URL} 已取 {fetched} 篇文章解析后 0 观测行"
            "（正文版式已变或全部为附件-only 文章）"
        )

    rows.sort(key=lambda r: (r["stat_period"], r["indicator"]))
    return rows[-limit:]


if __name__ == "__main__":  # 冒烟：python3 spiders/sac-securities/spider.py
    import pprint

    pprint.pp(run_sac_securities(limit=6))
