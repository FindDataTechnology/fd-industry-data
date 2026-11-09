"""spiders/ico-coffee-price —— 国际咖啡组织（ICO）咖啡指示价（日度 PDF）。

数据表面（2026-11-07 实测；见同目录 README）：

- 当月文档：``GET https://ico.org/documents/I-CIP.pdf``（200 直连；www.ico.org
  302→ico.org）。当月逐日表：``In US cents/lb`` 表头下 5 列
  I-CIP / Colombian Milds / Other Milds / Brazilian Naturals / Robustas，
  行格式 ``2-Nov 254.88 348.47 329.01 288.59 158.01``；周末/假日为只有日期
  无数值的空行（跳过，不造数）。
- 月度归档（``/documents/prices/``），两种命名模式（2026-11-07 实测）：

  * ``I-CIP_<MM>.<YYYY>.pdf``（2026 命名）：2026-01..2026-09 全 200；
  * ``I-CIP_<MonthName><YYYY>.pdf``（2025 命名）：2025-09..2025-12 全 200；
  * 双 host：ico.org 与 icocoffee.org（两命名两 host 交叉均 200）；
  * **在线归档真实窗口只有 ~13 个月**：2025-09..2026-09 + 当月活文档；
    2025-01..2025-08、2024 及更早全 404；2026-10 实测尚未归档（404）——
    归档滞后于当月，缺口按月跳过并留痕，不判红、不造数；
  * 1990 年起的长序列官方只挂死链 xlsx，Excel 明文「on request」
    （stats@ico.org）——如实记为限制，不做 URL 猜测式回补。

口坑（实测逐一落实）：

- **表头列名与数值列有错位风险**（"Colombian Milds" 在文本层折成两行、列
  间距不均）：不按表头名对列，按行内 5 数值固定序解析，另加**列交叉验证
  守卫**——Robustas 恒最低、I-CIP 落在四组指标值域内；出界即抛
  ``RuntimeError``（判红，失败即红）。注意**不**校验「Colombian 恒最高」：
  实测 2026-04-07 Other Milds 高于 Colombian，那是行情不是列错位
  （2026-11-07 试跑抓到的真缺陷，已按可辩护不变量收紧）。
- **汇总行**（Average / High / Low / DoD Change）不是 ``D-Mon`` 日期形态，
  天然被日期正则排除；``DoD Change`` 带不会被数值全匹配。
- **年月归属**：日行只写 ``D-Mon``，年份取文档标题
  ``ICO Indicator Prices - <Month> <YYYY> (I-CIP)``；归档月还与 URL 期望月
  交叉校验（不一致判红，防错月文档混入）。
- **残行**（日期后有 1–4 个数值）＝版面异常，判红；0 数值＝周末/假日，跳过。
- **PDF 文本抽取**：poppler ``pdftotext -layout`` 子进程（魔法数判 PDF，
  缺二进制/非 PDF/非零退出一律判红）。本单元 stdlib 自包含、直调 pdftotext，
  不 import ``fd_industry_data.pdftext``（该平台层属另一未落地变更，避免
  未合入依赖把本单元带红；Dockerfile runner 阶段已装 poppler-utils）。
- **网络策略**：直连优先（显式禁用环境代理）；网络级失败经代理
  ``http://127.0.0.1:7890`` 重试一次（与 restaurant-rpi 同款兜底；本机实测
  直连即通）。每 URL 至多两次尝试、30s 超时。
- **limit 语义** = 返回行数上限：按 ``day`` 升序确定性排序后截取。

接入：静默合入（manifest 不写 schedule），试跑验证后由人工门点亮。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SOURCE = "ico-coffee-price"
CURRENT_URL = "https://ico.org/documents/I-CIP.pdf"
ARCHIVE_HOSTS = ("https://ico.org", "https://icocoffee.org")
ARCHIVE_PATH = "/documents/prices/"
PROXY_URL = "http://127.0.0.1:7890"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf, */*",
}
TIMEOUT = 30  # 秒；单 URL 单次尝试（直连 + 代理兜底共至多两次）

PDF_MAGIC = b"%PDF-"
FIELDS = ("icip", "colombian_milds", "other_milds", "brazilian_naturals", "robustas")

_TITLE_RE = re.compile(
    r"ICO\s+Indicator\s+Prices\s+-\s+([A-Za-z]+)\s+(20\d{2})\s+\(I-CIP\)"
)
# 数据行：D-Mon + 恰好 5 个数值（5 列指示价一个不缺才收）
_ROW_RE = re.compile(
    r"^\s*(\d{1,2})-([A-Za-z]{3})\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+"
    r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*$"
)
# 日期 + 1–4 个数值的残行：版面/解析异常，判红而非吞掉
_PARTIAL_ROW_RE = re.compile(
    r"^\s*(\d{1,2})-([A-Za-z]{3})\s+(-?\d+(?:\.\d+)?)(?:\s+-?\d+(?:\.\d+)?){1,3}\s*$"
)
_ANCHOR = "In US cents/lb"
_MONTH_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAME = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November",
    12: "December",
}


def _fetch(url: str) -> bytes:
    """GET 一个 https 资源返回原始字节：直连优先，网络级失败经代理重试一次。"""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
            return resp.read()
    except Exception as direct_exc:  # noqa: BLE001 - 直连失败才走代理兜底
        proxy = urllib.request.ProxyHandler({"http": PROXY_URL, "https": PROXY_URL})
        try:
            with urllib.request.build_opener(proxy).open(req, timeout=TIMEOUT) as resp:  # noqa: S310
                return resp.read()
        except Exception as proxy_exc:  # noqa: BLE001
            raise RuntimeError(
                f"ico-coffee-price: 取数失败（{url}）— 直连: "
                f"{type(direct_exc).__name__}: {direct_exc}；代理: "
                f"{type(proxy_exc).__name__}: {proxy_exc}"
            ) from proxy_exc


def _pdf_text(data: bytes, url: str) -> str:
    """PDF 载荷 → ``pdftotext -layout`` 文本。非 PDF / 无二进制 / 非零退出判红。"""
    if not bytes(data[:1024]).lstrip().startswith(PDF_MAGIC):
        raise RuntimeError(f"ico-coffee-price: 非 PDF 载荷（{url}）——上游可能改版或反爬")
    if shutil.which("pdftotext") is None:
        raise RuntimeError(
            "ico-coffee-price: pdftotext 不存在——运行镜像需安装 poppler-utils"
            "（Dockerfile runner 阶段）"
        )
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        proc = subprocess.run(  # noqa: S603 - 固定二进制 + 常量参数
            ["pdftotext", "-layout", "-enc", "UTF-8", str(tmp_path), "-"],
            capture_output=True, timeout=120, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ico-coffee-price: pdftotext 超时（{url}）") from exc
    finally:
        tmp_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()[:200]
        raise RuntimeError(
            f"ico-coffee-price: pdftotext 退出码 {proc.returncode}（{url}）：{detail}"
        )
    return proc.stdout.decode("utf-8", "replace")


def _title_month_year(text: str, url: str) -> tuple[int, int]:
    """从文档标题取 (year, month)；缺失/不可解析判红（年月归属不允许猜）。"""
    m = _TITLE_RE.search(text)
    if not m:
        raise RuntimeError(
            f"ico-coffee-price: 文档标题不可解析（{url}）——"
            "预期 'ICO Indicator Prices - <Month> <YYYY> (I-CIP)'，布局可能已变"
        )
    month = _MONTH_NUM.get(m.group(1)[:3].lower())
    if month is None:
        raise RuntimeError(f"ico-coffee-price: 标题月份不可识别（{url}）: {m.group(1)!r}")
    return int(m.group(2)), month


def _check_columns(day: str, values: list[float], url: str) -> None:
    """列交叉验证守卫：Robustas 恒最低 + I-CIP 落在四组指标值域内。

    5 列顺序 [I-CIP, Colombian, Other, Brazilian, Robustas]；表头与数值列的
    错位（实测风险）会破坏该恒序，出界判红而不是落错列入库。

    为什么**不**要求 "Colombian 恒为最高"：这是行情假设而非结构不变量——实测
    2026-04-07 的 Other Milds(324.77) 就高于 Colombian(323.61)，按旧守卫整月判红、
    拒收真实数据。可辩护的不变量只有两条：

    * Robustas 是唯一的 canephora 组、报价结构性低于三类 arabica（行业差价基础）；
    * I-CIP 是四组指标的综合价，必须落在四组值的 [min, max] 区间内。

    列错位会把数值随机打散，这两条足以拦住；反过来不会因为行情倒挂误杀。
    """
    icip, _colom, _other, _brazil, robusta = values
    if not all(v > 0 for v in values):
        raise RuntimeError(
            f"ico-coffee-price: {day} 出现非正值 {values}（{url}）——列错位或源数据异常"
        )
    lo, hi = min(values[1:]), max(values[1:])
    if min(values) != robusta or not (lo <= icip <= hi):
        raise RuntimeError(
            f"ico-coffee-price: {day} 列交叉验证失败 {values}"
            f"（要求 Robustas=最低 {robusta}、I-CIP {icip} 落在四组值域 [{lo}, {hi}]）"
            f"（{url}）——表头/数值列错位或源数据异常"
        )


def _parse_document(data: bytes, url: str,
                    expect: tuple[int, int] | None = None) -> list[dict]:
    """一份月度 PDF → [{month, day, <FIELDS>...}]（空行跳过，残行判红）。"""
    text = _pdf_text(data, url)
    year, month = _title_month_year(text, url)
    if expect is not None and (year, month) != tuple(expect):
        raise RuntimeError(
            f"ico-coffee-price: 文档月份 {year}-{month:02d} 与期望 "
            f"{expect[0]}-{expect[1]:02d} 不一致（{url}）——归档命名与内容漂移"
        )
    lines = text.splitlines()
    anchors = [i for i, ln in enumerate(lines) if ln.strip() == _ANCHOR]
    if not anchors:
        raise RuntimeError(
            f"ico-coffee-price: 未找到 'In US cents/lb' 表头锚（{url}）——布局可能已变"
        )
    anchor = anchors[-1]  # 首个在图表纵轴区，最后一个是数据表头
    rows: dict[str, dict] = {}
    for ln in lines[anchor + 1:]:
        if "International Coffee Organization" in ln:
            break  # 表尾版权行之后是注释/说明，不再有数据
        m = _ROW_RE.match(ln)
        if m is None:
            if _PARTIAL_ROW_RE.match(ln):
                raise RuntimeError(
                    f"ico-coffee-price: 残行（日期 + 1–4 数值，预期 5 列）: {ln!r}"
                    f"（{url}）——版面异常，请人工复核解析规则"
                )
            continue  # 表头折行 / 周末假日空行 / 汇总行（不匹配日期形态）
        day_num, mon3 = int(m.group(1)), _MONTH_NUM[m.group(2).lower()]
        if mon3 != month:
            raise RuntimeError(
                f"ico-coffee-price: 行月份 {m.group(2)} 与文档月份 {month} 不一致"
                f"（{url}）: {ln!r}"
            )
        values = [float(m.group(i)) for i in range(3, 8)]
        day = f"{year:04d}-{month:02d}-{day_num:02d}"
        _check_columns(day, values, url)
        rows.setdefault(day, {
            "month": f"{year:04d}-{month:02d}",
            "day": day,
            **dict(zip(FIELDS, values)),
            "source_url": url,
        })
    return [rows[d] for d in sorted(rows)]


def _archive_urls(year: int, month: int) -> list[str]:
    """一个归档月的候选 URL：两种命名 × 双 host（实测序：MM.YYYY 命名先）。"""
    names = [f"I-CIP_{month:02d}.{year}.pdf", f"I-CIP_{_MONTH_NAME[month]}{year}.pdf"]
    return [f"{host}{ARCHIVE_PATH}{name}" for name in names for host in ARCHIVE_HOSTS]


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def _collect(limit: int, months_back: int) -> list[dict]:
    """当月活文档 + 近 months_back 个归档月 → 按 day 升序的行列表。"""
    now = datetime.now(timezone.utc)
    by_day: dict[str, dict] = {}

    current = _fetch(CURRENT_URL)  # 当月文档不可得 → 判红（主数据面）
    for row in _parse_document(current, CURRENT_URL):
        by_day.setdefault(row["day"], row)

    skipped: list[str] = []
    for i in range(1, months_back + 1):
        year, month = _shift_month(now.year, now.month, -i)
        got = False
        for url in _archive_urls(year, month):
            try:
                data = _fetch(url)
            except RuntimeError:
                continue  # 404 / 网络失败 → 下一个候选（命名/host）
            for row in _parse_document(data, url, expect=(year, month)):
                by_day.setdefault(row["day"], row)
            got = True
            break
        if not got:
            skipped.append(f"{year}-{month:02d}")
    if skipped:
        # 归档缺口留痕（stderr 可见于运行日志），不判红：在线归档本就滞后/有限
        import sys
        print(
            "ico-coffee-price: 归档月未获取（404/漂移，跳过不造数）: "
            + ", ".join(skipped),
            file=sys.stderr,
        )
    if not by_day:
        raise RuntimeError(
            "ico-coffee-price: 当月文档与全部归档候选均未取得任何数据行——失败即红"
        )

    ordered = [by_day[d] for d in sorted(by_day)]
    now_iso = now.isoformat()
    for row in ordered:
        row["scraped_at"] = now_iso
    return ordered[:limit]


def run_ico_coffee_price(limit: int = 500, months_back: int = 2) -> list[dict]:
    """取 ICO 咖啡指示价（I-CIP 综合 + 4 组分指示价）日度行（宽表）。

    Args:
        limit: 返回行数上限（按 ``day`` 升序确定性截取）。
        months_back: 除当月活文档外再回取的归档月数（在线归档实测仅
            2025-09..2026-09 ~13 个月；更早月份 404，按月跳过留痕，不造数）。

    Returns:
        list of dict：每日一行 {month（YYYY-MM）, day（YYYY-MM-DD）, icip,
        colombian_milds, other_milds, brazilian_naturals, robustas,
        source_url, scraped_at}；周末/假日（源表空行）不出行。

    Raises:
        RuntimeError: 当月文档不可得、PDF 解析失败、列交叉验证出界、或全部
            候选无一产出（失败即红）。
        ValueError: ``limit`` / ``months_back`` 非法。
    """
    try:
        limit = int(limit)
        months_back = int(months_back)
    except (TypeError, ValueError):
        raise ValueError(
            f"limit/months_back must be ints, got {limit!r}/{months_back!r}"
        ) from None
    if limit <= 0:
        return []
    if months_back < 0:
        raise ValueError(f"months_back must be >= 0, got {months_back}")
    return _collect(limit, months_back)


if __name__ == "__main__":  # 冒烟：python3 spiders/ico-coffee-price/spider.py
    print(json.dumps(run_ico_coffee_price(limit=6), ensure_ascii=False, indent=2))
