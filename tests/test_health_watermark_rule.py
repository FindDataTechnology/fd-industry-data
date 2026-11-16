"""「天天成功但数据期冻结」必须可见（2026-11-16）。

`watermark.py` 是 `harden-crawl-delivery` D5 的产物，docstring 写着要防什么：
「每天重写同一个旧月份的 spider 行数漂亮、水位冻结」。它也确实被 `inspector`
调用来填工单的 `evidence.watermark`——**但 `classify()` 从不读它**，于是判据只进
证据、不进判决。

实历（2026-11-16 同一时刻三条）：`carbon-monitor` 的 cron 当天成功（`rows=100`）而
库里最新数据期停在 **2026-08-31**；`entsoe-e`（spider 取滚动 7 个 UTC 日，本该天天
有新期）停在 10-20；`zgdypf-boxoffice` 停在 10-22。巡检报 `healthy 126 / seen 148`，
`absent_sources` 只有 6 个且不含这三条。全系统唯一注意到的是看板侧的证据年龄行。

四条纪律，逐条有用例：

1. **只在本来会判健康时生效**：已有失败样本的源，类别一丝不许变（否则会抢走
   反爬/网络/结构规则的单）。
2. **判不出不升级**：未声明 `data_period`、`irregular`、值不可归一、粒度比节奏
   更粗——四种都保持原判。全仓多数源没声明水位字段。
3. **成对断言**：水位当前（`advance`）仍健康；落后 ≥2 周期才出单。
4. **断言打在判决上**，不是「证据里有这个字段」——那正是它此前一直绿的原因。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fd_industry_data.health import triage

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
DAY = 24.0


def _run(hours_ago: float, *, status: str = "success", rows: int | None = 100,
         watermark: str | None = None) -> triage.Run:
    return triage.Run(started_at=(NOW - timedelta(hours=hours_ago)).isoformat(),
                      status=status, rows=rows, watermark=watermark)


def _fresh_runs(watermark: str | None, n: int = 3) -> list[triage.Run]:
    """窗口内 n 次成功、都记着同一个水位（现实形态：每天跑，抓到的是同一批）。"""
    return [_run(h, watermark=watermark) for h in range(2, 2 + n * 12, 12)]


# ── 主线：冻结 ⇒ 不再健康 ────────────────────────────────────────────────


def test_frozen_watermark_turns_a_fresh_success_into_a_ticket():
    """carbon-monitor 的真实形态：跑得新鲜、行数达标、水位停在 77 天前。"""
    runs = _fresh_runs("2026-08-31")
    v = triage.classify("carbon-monitor", "spiders/carbon-monitor/", DAY, runs, NOW,
                        data_period="day")
    assert v.needs_human, "天天成功但数据期不前进，不能判健康"
    assert v.category is None, "分不出上游停发还是窗口没跟上，不猜类别"
    assert "rule1d" in v.rule_trace
    assert "2026-08-31" in v.rule_trace, "证据里要点名那个水位"
    assert "77" in v.rule_trace or "7" in v.rule_trace, "落后周期数要算出来"
    assert "上游" in v.suggested_action, "动作要先分辨上游是否真的停发"


def test_same_runs_without_the_data_period_stay_healthy():
    """成对的另一面：不传 data_period ⇒ 老行为（零回归）。"""
    runs = _fresh_runs("2026-08-31")
    v = triage.classify("carbon-monitor", "spiders/carbon-monitor/", DAY, runs, NOW)
    assert v.category is None and not v.needs_human


def test_current_watermark_stays_healthy():
    runs = _fresh_runs("2026-11-15")
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW, data_period="day")
    assert not v.needs_human
    assert "rule1:" in v.rule_trace, "这时应当走健康短路"


def test_a_monthly_source_one_month_behind_is_not_stalled():
    """节奏必须按源自己的：月频源落后 1 个月是正常的，不能按 daily 判。"""
    runs = _fresh_runs("2026-09")
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW, data_period="month")
    assert not v.needs_human
    stale = triage.classify("src", "spiders/src/", DAY, _fresh_runs("2026-06"), NOW,
                            data_period="month")
    assert stale.needs_human, "同一份数据放到月频上落后 5 个月，必须出单"


# ── 判不出不升级（四种） ────────────────────────────────────────────────


def test_undeclared_cadence_is_not_stalled():
    runs = _fresh_runs("2026-08-31")
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW, data_period="irregular")
    assert not v.needs_human, "irregular 没有节奏，判不了停滞——不能当健康失败"


def test_unparsable_watermark_is_not_stalled():
    runs = _fresh_runs("详见附件")
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW, data_period="day")
    assert not v.needs_human, "值不可归一是「判不出」，不是「停滞」"


def test_coarser_granularity_than_cadence_is_not_stalled():
    """年水位 + 月节奏：区间总是落在未来，按它判会永远「当前」（amis-market 实测）。"""
    runs = _fresh_runs("2024")
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW, data_period="month")
    assert not v.needs_human


def test_no_watermark_at_all_is_not_stalled():
    runs = _fresh_runs(None)
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW, data_period="day")
    assert not v.needs_human


# ── 不许抢走既有类别 ───────────────────────────────────────────────────


def test_failing_source_keeps_its_category_even_if_watermark_is_frozen():
    """反爬/网络/结构规则的单不能被这条抢走：失败样本在场时本规则不生效。"""
    runs = [_run(2, status="failed", watermark="2026-08-31"),
            _run(26, status="failed", watermark="2026-08-31"),
            _run(50, status="failed", watermark="2026-08-31")]
    runs[0] = triage.Run(started_at=runs[0].started_at, status="failed", rows=None,
                         error="403 forbidden", http_status=403, watermark="2026-08-31")
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW, data_period="day")
    assert v.category is not None, "有失败样本就必须照旧分类"
    assert "rule1d" not in v.rule_trace
