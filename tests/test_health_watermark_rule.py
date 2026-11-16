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


# ── 阈值按节奏给：工单正文里的判据必须是判决真的用的那个 ────────────────


def test_trace_carries_the_effective_day_threshold():
    """日频的判据是 10（周末 3 + 长假 8 + 延迟 1），不是 2。

    判据值与抽屉里的表对不上时，读工单的人会照 2 去理解「落后 47 天」这件事，
    处置方向就跟着错（「才两天没进来，等等看」）。
    """
    runs = _fresh_runs("2026-09-30")
    v = triage.classify("carbon-monitor", "spiders/carbon-monitor/", DAY, runs, NOW,
                        data_period="day")
    assert v.needs_human
    assert "判据 =10" in v.rule_trace, v.rule_trace
    assert "判据 =2" not in v.rule_trace


def test_a_friday_watermark_does_not_emit_a_ticket_on_monday():
    """周五的水位 + 周一巡检 ≠ 冻结（2026-11-16 生产实测的三条假红）。"""
    monday = datetime(2026, 11, 16, 5, 51, tzinfo=timezone.utc)   # 与那次 run 同刻
    runs = [triage.Run(started_at=(monday - timedelta(hours=h)).isoformat(),
                       status="success", rows=1200, watermark="2026-11-13")
            for h in (0.5, 12, 24, 36)]
    v = triage.classify("chinabond-yield-curve", "spiders/chinabond-yield-curve/",
                        DAY, runs, monday, data_period="day")
    assert not v.needs_human, v.rule_trace
    assert "rule1:" in v.rule_trace


# ── 落后很多但仍在推进 ⇒ 不出单（carbon-monitor 实历，2026-11-16） ──────────


def _lagged_runs(advancing: bool, n: int = 30, lag_days: int = 47):
    """n 天、每天都跑成功，水位恒定滞后 ``lag_days``。

    ``advancing=True`` 时水位每轮前进一天（上游恒定发布滞后，数据天天来）；
    ``False`` 时所有轮次都是同一个水位（真的停更）。
    """
    base = NOW.date() - timedelta(days=lag_days)
    runs = []
    for i in range(n):                       # i=0 最旧
        run_at = NOW - timedelta(days=n - 1 - i, hours=2)
        wm = base + timedelta(days=(i if advancing else 0))
        runs.append(triage.Run(started_at=run_at.isoformat(), status="success",
                               rows=100, watermark=wm.isoformat()))
    return runs


def test_structural_publication_lag_does_not_emit_a_ticket():
    """carbon-monitor：声明日频、上游恒定滞后 ~2 个月、水位天天前进。"""
    v = triage.classify("carbon-monitor", "spiders/carbon-monitor/", DAY,
                        _lagged_runs(advancing=True), NOW, data_period="day")
    assert not v.needs_human, v.rule_trace
    assert "rule1:" in v.rule_trace


def test_the_same_shape_frozen_does_emit_a_ticket():
    """成对：同一份「落后 47 天」，但水位不动 ⇒ 必须出单，且 trace 报推进率 0。"""
    v = triage.classify("carbon-monitor", "spiders/carbon-monitor/", DAY,
                        _lagged_runs(advancing=False), NOW, data_period="day")
    assert v.needs_human
    assert "rule1d" in v.rule_trace
    assert "推进率 =0.00x" in v.rule_trace, v.rule_trace


def test_trace_names_the_measured_rate():
    """出单时 trace 要带出推进率，否则读的人只能凭「落后 N 天」猜。"""
    runs = _lagged_runs(advancing=False)
    # 让水位在最后一轮前进 3 天：29 天跨度里推进 3 天 ⇒ 0.10x，低于及格线仍出单。
    # **别挑 17 天**——那是 0.59x，会把结论翻成健康，这条用例就什么都验不到了
    # （它要验的是「出单那条路上的 trace」，不是「不出单」）。
    runs[-1] = triage.Run(started_at=runs[-1].started_at, status="success", rows=100,
                          watermark=(NOW.date() - timedelta(days=44)).isoformat())
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW, data_period="day")
    assert v.needs_human
    assert "推进率 =0.10x" in v.rule_trace, v.rule_trace
