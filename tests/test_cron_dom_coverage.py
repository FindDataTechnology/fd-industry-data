"""`dom≥29` 的 cron 漏触发与误报率：用真实日历算，闰年与否必须区分。

背景：8 个源把 schedule 写成固定 29/30/31 日，这些日子在某些月份不存在，
CronJob 不补偿——那个月一次都不跑，且巡检因 data_period=irregular 直接跳过超期判定。
本测试钉住"按真实日历"这件事本身：拿名义日历（每月 30 天）算会得出不同答案。
"""

from __future__ import annotations

import calendar
import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/cron_dom_coverage.py"


def load():
    spec = importlib.util.spec_from_file_location("cron_dom_coverage", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    sys.modules["cron_dom_coverage"] = m
    assert spec.loader is not None
    spec.loader.exec_module(m)
    return m


class CronDomTests(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_dom_31_fires_only_in_seven_months(self):
        """dom=31：只有 1/3/5/7/8/10/12 有 31 日 → 一年 7 次。"""
        trig = self.m.expand_year(31, 2026)
        self.assertEqual([d.month for d in trig], [1, 3, 5, 7, 8, 10, 12])

    def test_dom_29_is_year_dependent(self):
        """dom=29：2026 非闰年 2 月缺（11 次），2028 闰年 2 月有（12 次）。

        只看一年会得出相反结论：2028 看是"完全正常"，2026 看是"每月都缺"。
        """
        self.assertFalse(calendar.isleap(2026))
        self.assertTrue(calendar.isleap(2028))
        self.assertEqual(len(self.m.expand_year(29, 2026)), 11)
        self.assertEqual(len(self.m.expand_year(29, 2028)), 12)
        self.assertEqual(self.m.analyse(29, 2026)["fp_rate"], 23/365)
        self.assertEqual(self.m.analyse(29, 2028)["fp_rate"], 0.0)

    def test_dom_30_misses_only_february(self):
        self.assertEqual(self.m.analyse(30, 2026)["missing_months"], [2])
        self.assertEqual(self.m.analyse(30, 2026)["triggers"], 11)

    def test_jp_estat_has_the_worst_gap(self):
        """jp-estat（dom=31）最大间隔 61 天，按 month 频（35 天）判会误报 35% 的天数。"""
        a = self.m.analyse(31, 2026)
        self.assertEqual(a["triggers"], 7)
        self.assertEqual(a["max_gap_days"], 61)
        self.assertEqual(a["days_over_tolerance"], 123)
        self.assertGreater(a["fp_rate"], 0.3)
        self.assertLess(a["fp_rate"], 0.4)

    def test_trigger_day_is_never_counted_as_overdue(self):
        """触发日本身不能算超期。

        早先版本先判超期、后更新 last，于是 dom=29 的 03-29（明明触发了）因为
        "距上次 59 天"被算进超窗，每个超窗间隔多算 1 天（dom=29 多 1、dom=31 多 5）。
        """
        from datetime import date, timedelta
        a = self.m.analyse(29, 2026)
        # dom=29 在 2026 只有一个超窗间隔 01-29→03-29；超窗日应为 03-06…03-28 共 23 天
        self.assertEqual(a["days_over_tolerance"], 23)
        self.assertEqual(a["max_gap_days"], 59)

    def test_february_triggers_are_zero_for_all_three_doms(self):
        for dom in (29, 30, 31):
            self.assertEqual(self.m.analyse(dom, 2026)["feb_triggers"], 0, dom)


if __name__ == "__main__":
    unittest.main()
