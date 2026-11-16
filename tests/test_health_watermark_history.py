"""宽窗水位历史是承重的那一半（watermark-history-for-coarse-cadence 3.x）。

上一笔（watermark-judge-advance-rate）给「水位冻结」加了第二合取项
「落后达阈值 **且** 推进率 < 0.5」，修好了日频（usgs-nwis 退出），但月频
这一类露了出来：`uk-hpi` 的 30 天遥测窗 < 月频最小可测跨度（2 个周期＝
61 天）⇒ 推进率恒为 ``None`` ⇒ 永久假红。本 change 读一份 400 天宽窗历史
喂给推进率。这些用例钉住三件事：

1. **历史是承重的**：同一份落后 3 个月但仍在推进的月频源，给历史 ⇒ 健康，
   不给 ⇒ 冻结（成对，3.1）。缺了任何一半，另一个结论都没有证据。
2. **切片必须存在**（3.3）：400 天整窗会把「最近 150 天停更」稀释成
   推进率 0.55——切片后才是 0。红证见 tasks.md 3.5。
3. **判不出如实说**（3.4）：历史为空/不可解析 ⇒ 退回窗口内旧口径，
   判词里写「推进率 =不可测」，不把「没读到」当「没有推进」。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fd_industry_data.health import inspector, triage

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
DAY = 24.0


def _run(hours_ago: float, *, watermark: str) -> triage.Run:
    return triage.Run(started_at=(NOW - timedelta(hours=hours_ago)).isoformat(),
                      status="success", rows=100, watermark=watermark)


def _fresh_runs(watermark: str, n: int = 3) -> list[triage.Run]:
    """窗口内 n 次成功、都记着同一个水位（每天在跑、抓到的还是那个旧月）。"""
    return [_run(h, watermark=watermark) for h in range(2, 2 + n * 12, 12)]


def _hist(days_ago: int, raw: str) -> tuple[datetime, str]:
    return (NOW - timedelta(days=days_ago), raw)


def _ukhpi_history() -> list[tuple[datetime, str]]:
    """uk-hpi 形态（月频、恒定落后 3 个月、每月推进一期）：run 时刻 = 水位月 +3。"""
    return [
        _hist(314, "2025-10"), _hist(283, "2025-11"), _hist(255, "2025-12"),
        _hist(224, "2026-01"), _hist(194, "2026-02"), _hist(163, "2026-03"),
        _hist(133, "2026-04"), _hist(102, "2026-05"), _hist(71, "2026-06"),
        _hist(41, "2026-07"), _hist(10, "2026-08"),
    ]


# ── 3.1 月频落后 3 个月但在推进 ⇒ 健康；成对的另一半证明历史承重 ──────────


def test_monthly_three_months_behind_but_advancing_is_healthy():
    """uk-hpi 形态：落后 2.5 个周期 ≥ 阈值 2，但宽窗推进率 1.0x ≥ 0.5。"""
    runs = _fresh_runs("2026-08")
    v = triage.classify("uk-hpi", "spiders/uk-hpi/", DAY, runs, NOW,
                        data_period="month", watermark_history=_ukhpi_history())
    assert not v.needs_human, (
        "月频落后 3 个月但每月都在推进，不能判冻结——这正是 uk-hpi 的假红")
    assert "rule1:" in v.rule_trace, "应走健康短路"


def test_same_runs_without_history_still_freeze():
    """成对：不传历史 ⇒ 30 天窗内推进率 0 ⇒ 仍冻结（历史是承重的那一半）。"""
    runs = _fresh_runs("2026-08")
    v = triage.classify("uk-hpi", "spiders/uk-hpi/", DAY, runs, NOW,
                        data_period="month")
    assert v.needs_human, "没有历史时必须保持旧行为（冻结）——否则 3.1 的健康无从对照"
    assert "不可测" in v.rule_trace, "30 天窗对月频测不出推进率（跨度<2 周期），按落后判"


# ── 3.2 月频真停更 ⇒ 冻结 ────────────────────────────────────────────────


def test_monthly_really_stalled_freezes_even_with_history():
    """历史 400 天全停在 2026-06：推进率 0，历史救不了真停更。"""
    history = [_hist(d, "2026-06") for d in (10, 71, 102, 133, 164, 195, 255, 314)]
    runs = _fresh_runs("2026-06")
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW,
                        data_period="month", watermark_history=history)
    assert v.needs_human, "400 天历史全停在同一个月份 = 真停更"
    assert "rule1d" in v.rule_trace
    assert "0.00x" in v.rule_trace, "推进率 0 要如实出现在判词里"


# ── 3.3 切片边界：长窗不许稀释近期停更 ───────────────────────────────────


def test_slice_boundary_recent_stop_is_not_diluted():
    """前 250 天推进、最近 150 天停：切片（~122 天）后窗口里全是停更点。"""
    history: list[tuple[datetime, str]] = []
    # 推进段：400→150 天前，每月一期（2025-10 → 2026-06）
    for d, raw in ((314, "2025-10"), (283, "2025-11"), (255, "2025-12"),
                   (224, "2026-01"), (194, "2026-02"), (163, "2026-03"),
                   (150, "2026-06")):
        history.append(_hist(d, raw))
    # 停更段：140→10 天前，水位停在 2026-06
    history += [_hist(d, "2026-06") for d in range(140, 9, -10)]
    runs = _fresh_runs("2026-06")
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW,
                        data_period="month", watermark_history=history)
    assert v.needs_human, (
        "最近 150 天停更必须判冻结——整窗测会把推进率稀释到 ≥0.5 而放行")
    assert "0.00x" in v.rule_trace


# ── 3.4 历史为空 / 不可解析 ⇒ 退回旧行为，判词里推进率=不可测 ─────────────


def test_empty_history_falls_back_and_reports_unmeasurable():
    runs = _fresh_runs("2026-08")
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW,
                        data_period="month", watermark_history=[])
    assert v.needs_human, "退回窗口内：月频在 30 天窗测不出推进率 ⇒ 按落后判"
    assert "不可测" in v.rule_trace, "判不出要如实写「不可测」，不写 0"


def test_unparseable_history_falls_back_and_reports_unmeasurable():
    runs = _fresh_runs("2026-08")
    history = [_hist(10, "not-a-period"), _hist(30, "??")]
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW,
                        data_period="month", watermark_history=history)
    assert v.needs_human
    assert "不可测" in v.rule_trace


def test_unparseable_history_still_lets_an_advancing_source_stay_healthy_when_window_can_measure():
    """不可解析历史退回窗口内；窗口能测且在推进时仍是健康（口径不因退回而收紧）。"""
    # 日频源：窗口 30 天 ≥ 2 个周期，窗口内水位在推进 ⇒ 窗口样本自己就能测出。
    runs = [_run(2, watermark="2026-11-15"), _run(26, watermark="2026-11-14"),
            _run(50, watermark="2026-11-13")]
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW,
                        data_period="day", watermark_history=[_hist(5, "junk")])
    assert not v.needs_human, "历史不可解析不该把一个可测且在推进的源判红"


# ---------------------------------------------------------------------------
# 覆盖度一行（2026-11-17-watermark-history-coverage-signal）
#
# 判据：推进率只在历史 ≥2 点时生效，否则**静默**退回 30 天窗；退回时粗节奏源的
# 判词是「推进率 =不可测」，与「源真停更」在所有可见读数里同形。这一行只增可见性，
# 所以下面的断言都打在**这一行的措辞**上，且必须能分开「历史为空」与「点数不足」。
# ---------------------------------------------------------------------------

def test_coverage_line_says_empty_history_explicitly():
    """真的空 ⇒ 打「源 0 / 点 0」，不给任何「一切正常」的暗示。"""
    line = inspector.describe_watermark_history({})
    assert "源 0" in line and "点 0" in line
    assert "≥2" not in line, "空历史不该说每源都够"


def test_coverage_line_names_single_point_sources():
    """只有 1 点的源必须被点名——它的推进率会退回窗口口径。"""
    hist = {"alpha": [(NOW, "2026-08")], "beta": [(NOW, "2026-10"), (NOW - timedelta(days=40), "2026-09")]}
    line = inspector.describe_watermark_history(hist)
    assert "源 2" in line and "点 3" in line
    assert "alpha" in line and "beta" not in line
    assert "剩余退回窗口内口径" in line or "退回窗口内口径" in line
    assert "1 个源只有 1 个可归一点" in line


def test_coverage_line_is_quiet_when_every_source_has_two_points():
    hist = {"alpha": [(NOW, "2026-08"), (NOW - timedelta(days=40), "2026-07")]}
    line = inspector.describe_watermark_history(hist)
    assert "每源都有 ≥2 个可归一点" in line
    assert "退回窗口内口径" not in line


def test_coverage_line_caps_the_sample_and_states_the_total():
    hist = {f"s{i}": [(NOW, "2026-08")] for i in range(6)}
    line = inspector.describe_watermark_history(hist)
    assert "6 个源只有 1 个可归一点" in line
    assert "…等 6 个" in line
    named = [f"s{i}" for i in range(6) if f"s{i}" in line]
    assert len(named) <= 5, f"样例最多 5 个，实际点了 {len(named)}"


def test_window_source_missing_from_history_is_called_out_as_a_reader_problem():
    """同一条 SQL 的更宽窗口必然包含窗口内每一行 ⇒ 缺席只能出在读方，单列一档点名。

    这是比点数统计更硬的不变量：它把「历史为空」从「点数不足」里彻底分出来。
    """
    hist = {"beta": [(NOW, "2026-10"), (NOW - timedelta(days=40), "2026-09")]}
    line = inspector.describe_watermark_history(hist, window_sources={"alpha", "beta"})
    assert "⚠" in line and "alpha" in line
    assert "读方有问题" in line
