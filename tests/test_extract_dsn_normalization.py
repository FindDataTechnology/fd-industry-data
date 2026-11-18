"""`extract_*.py` 的 DSN 归一口径回归（2026-11-15 立）。

**为什么需要这条测试**：生产 Secret `FD_OPEN_DATA_MCP_DATABASE_URL` 的值是
SQLAlchemy 形态 `postgresql+psycopg2://user:pass@host:port/db`，而
`psycopg2.connect()` **不认** `+psycopg2` 方言前缀，直接传会抛
`ProgrammingError: invalid dsn: missing "=" after "postgresql+psycopg2://…"`。

这个缺陷在本仓藏了很久：全仓 **28 个脚本**都是裸 `psycopg2.connect(args.dsn)`，
而它们之所以「看起来能跑」，是因为一直被**手工**用另一份 DSN 调用；一旦按生产
Secret 接进定时作业就当场连不上（2026-11-15 `extract_batch1`/`extract_batch2`
在集群内 dry-run 里就是这么失败的，而它们此前从未进过日频作业）。

测试的形态刻意是**双向台账**：
  1. 作业真的会跑的脚本（`JOB_SCRIPTS`）**必须**归一化 —— 新增一条作业命令而
     忘了归一化，这里直接红；
  2. 已归一化的脚本不许退回去 —— `PENDING_DSN_FIX` 与实测集合必须**相等**：
     修好一个却没从名单里划掉会红（防止「以为修了」），新写一个裸连的也会红。
省掉第 2 条，名单会变成一句永远不会被核对的注释。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

# 两条作业（semantic-extract-daily / semantic-extract-batch）命令里出现的脚本。
# 这些是**生产会跑**的，要求最严。
JOB_SCRIPTS = (
    "extract_batch1.py", "extract_batch2.py",
    "extract_eastmoney.py", "extract_uk_hpi.py", "extract_rates.py",
    "extract_ncd.py", "extract_lpr.py", "extract_usgs_nwis.py",
    "extract_nasa_power.py", "extract_weather_daily.py",
    # 2026-11-15 第二批：领域 wave 抽取器（覆盖 R2 里 5 个「已映射但读面
    # 落后」的源）。它们此前**一次都没在集群里跑过**，原因就是这个 DSN。
    # 2026-11-15 第二批：领域 wave 抽取器（覆盖 R2 里 5 个「已映射但读面
    # 落后」的源）。它们此前**一次都没在集群里跑过**，原因就是这个 DSN。
    # 2026-11-19 起这四个进了常驻作业（change
    # schedule-wave-observation-extraction）：从「理论上会被调用」变成
    # 「每天 18:10 真的被调用」——DSN 不归一化会在第一次调度就炸。
    "extract_agriculture_wave1.py", "extract_flower_wave1.py",
    "extract_hotel_wave0.py", "extract_pharma.py",
    "extract_restaurant_wave0.py",
)

# 已知仍用裸 DSN 的 `extract_*.py`（2026-11-15 实测）。**修好一个就从这里划掉一个**：
# 名单与实测集合必须相等，两个方向都会红。
PENDING_DSN_FIX: set[str] = set()

NORMALIZED = re.compile(r"parse_dsn|_dsn\(|postgres\(\?:ql\)")


def _connects_raw(path: Path) -> bool:
    """该脚本是否把 DSN 直接交给 psycopg2（且全文没有任何归一化痕迹）。"""
    text = path.read_text()
    if not re.search(r"psycopg2\.connect\(", text):
        return False
    return NORMALIZED.search(text) is None


def test_job_scripts_normalize_the_dsn():
    bad = [n for n in JOB_SCRIPTS if _connects_raw(SCRIPTS / n)]
    assert not bad, (
        "这些脚本会被定时作业用生产 Secret 调用，但把 "
        "`postgresql+psycopg2://` 直接交给了 psycopg2：\n  "
        + "\n  ".join(bad)
        + "\n修法：`from _semextract import parse_dsn` 后 `psycopg2.connect(parse_dsn(dsn))`"
    )


def test_pending_dsn_fix_ledger_is_exact():
    """名单必须等于实测集合 —— 修好的要划掉，新裸连的要加进来。"""
    measured = {p.name for p in sorted(SCRIPTS.glob("extract_*.py")) if _connects_raw(p)}
    extra = sorted(measured - PENDING_DSN_FIX)
    stale = sorted(PENDING_DSN_FIX - measured)
    assert not extra, f"新出现裸 DSN 的脚本（归一化它，别留在名单外）：{extra}"
    assert not stale, f"这些已不再裸连，请从 PENDING_DSN_FIX 划掉：{stale}"


STANDALONE = re.compile(r'if __name__ == "__main__"')
CONNECTS = re.compile(r"psycopg2\.connect\(")


def _standalone_connectors() -> dict[str, bool]:
    """{相对路径: 是否归一化} —— 所有「带 `__main__` 且自己连库」的脚本。

    **为什么定义域要扩到这一族**（2026-11-15 第三轮）：第一版只 glob 顶层
    `extract_*.py`，于是 `hotel_extractors/ashare_operating.py`、`hkex_operating.py`、
    `sec_8k_operating.py` 这三个**有自己的 `__main__`** 的公司频道模块全在视野外 ——
    而它们正是 R2 里 `cninfo-operating` 的产出者，一旦手工/作业跑就会撞同一句
    `invalid dsn: missing "=" after …`。判据改成「能被人或作业直接跑、且自己连库」，
    就与「会不会拿到生产 Secret」这一件事对齐了。
    """
    out: dict[str, bool] = {}
    for f in sorted(SCRIPTS.rglob("*.py")):
        if any(x in f.parts for x in (".venv", "node_modules", "__pycache__", ".git")):
            continue
        text = f.read_text(errors="ignore")
        if not (STANDALONE.search(text) and CONNECTS.search(text)):
            continue
        out[str(f.relative_to(SCRIPTS))] = NORMALIZED.search(text) is not None
    return out


def test_every_standalone_script_normalizes_the_dsn():
    """带 `__main__` 且自己连库的脚本必须归一化（2026-11-15 全仓清零）。"""
    found = _standalone_connectors()
    assert found, "一个独立可跑的连库脚本都没扫到 —— 扫错目录了，别当成通过"
    bad = sorted(n for n, ok in found.items() if not ok)
    assert not bad, (
        "这些脚本能直接跑、自己连库，却把 `postgresql+psycopg2://` 直接交给 "
        "psycopg2（会报 `invalid dsn: missing \"=\" after …`）：\n  "
        + "\n  ".join(bad)
        + "\n修法：抄 `scripts/extract_batch1.py` 的 `_dsn()`，把 connect 处包一层"
    )


def test_every_extract_script_normalizes_the_dsn():
    """2026-11-15 收口：全仓 `extract_*.py` 都归一化了 DSN，名单已清空。

    名单清空之后仍要留一条正向断言，否则「一个都没漏」与「测试被删掉了」
    长得一模一样：这里要求每个 `extract_*.py`（只要它连库）都留归一化痕迹。
    """
    conntects = [p.name for p in sorted(SCRIPTS.glob("extract_*.py"))
                 if re.search(r"psycopg2\.connect\(", p.read_text())]
    assert conntects, "一个连库脚本都没扫到 —— 扫错目录了，别当成通过"
    bad = [n for n in conntects if _connects_raw(SCRIPTS / n)]
    assert not bad, f"这些 extract 脚本仍是裸 DSN：{bad}"
