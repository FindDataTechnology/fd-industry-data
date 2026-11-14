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
)

# 已知仍用裸 DSN 的 `extract_*.py`（2026-11-15 实测）。**修好一个就从这里划掉一个**：
# 名单与实测集合必须相等，两个方向都会红。
PENDING_DSN_FIX = {
    "extract_agriculture_wave1.py",
    "extract_flower_wave1.py",
    "extract_hotel_wave0.py",
    "extract_pharma.py",
    "extract_restaurant_wave0.py",
}

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


@pytest.mark.parametrize("name", sorted(PENDING_DSN_FIX))
def test_pending_scripts_still_exist(name):
    """名单里的文件必须真的还在（改名/删除后名单要跟着动）。"""
    assert (SCRIPTS / name).exists(), f"{name} 不存在，更新 PENDING_DSN_FIX"
