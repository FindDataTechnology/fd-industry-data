"""--window-auto: k8s AppSet CronJob 路径的首爬窗口注入（与 dispatcher 同口径）。"""
from unittest import mock

from fd_industry_data import runner_cli


def test_auto_window_for_never_succeeded_source():
    fake = mock.MagicMock()
    with mock.patch.object(runner_cli, "dispatch", create=True) as d:
        d.connect.return_value = fake
        d.successful_run_sources.return_value = set()
        d.first_crawl_window.return_value = {"window_start": "2025-11-01", "window_end": "2026-11-06"}
        # 走真实 import 路径：patch runner_cli 里 import 的 dispatch 模块
        import importlib
        dispatch = importlib.import_module("fd_industry_data.dispatch")
        with mock.patch.object(dispatch, "connect", return_value=fake), \
             mock.patch.object(dispatch, "successful_run_sources", return_value=set()), \
             mock.patch.object(dispatch, "first_crawl_window", return_value={"window_start": "2025-11-01", "window_end": "2026-11-06"}):
            s, e = runner_cli._auto_first_crawl_window("fresh-source")
    assert (s, e) == ("2025-11-01", "2026-11-06")


def test_auto_window_noop_for_established_source():
    fake = mock.MagicMock()
    import importlib
    dispatch = importlib.import_module("fd_industry_data.dispatch")
    with mock.patch.object(dispatch, "connect", return_value=fake), \
         mock.patch.object(dispatch, "successful_run_sources", return_value={"old-source"}), \
         mock.patch.object(dispatch, "first_crawl_window") as fcw:
        s, e = runner_cli._auto_first_crawl_window("old-source")
    assert (s, e) == ("", "")
    fcw.assert_not_called()


def test_auto_window_degrades_on_db_failure(capsys):
    import importlib
    dispatch = importlib.import_module("fd_industry_data.dispatch")
    with mock.patch.object(dispatch, "connect", side_effect=RuntimeError("db down")):
        s, e = runner_cli._auto_first_crawl_window("any-source")
    assert (s, e) == ("", "")
    assert "window-auto probe failed" in capsys.readouterr().err


def test_auto_noop_empty_strings_do_not_fail_parse():
    # window-auto 的 ("", "") no-op 不得被当成「单边坏窗口」误报
    import pytest
    assert runner_cli._parse_window("", "") is None
    with pytest.raises(ValueError, match="need both"):
        runner_cli._parse_window("2025-11-01", "")
