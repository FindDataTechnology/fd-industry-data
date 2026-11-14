"""失败通知步：位置与两个门都是判据，不是排版。

2026-11-12/13/14 三晚每日巡检验收全灭（`cancelled`），三天零工单投出，
**没有任何通道发过一封邮件**——恢复靠人翻 GHA run 列表。所以这一条不是
"顺手加个通知"，它补的是整条线唯一的出口。三条判据各自都踩过：

1. **位置必须在最后**。我第一版按 `echo "push failed after retries"; exit 1`
   定位插入，而这个锚点在文件里出现两次（`Commit & push tickets` 与
   `Commit delivery receipts` 各一次，后者那次在文件末尾且**没有换行符**），
   结果步落到了 **Deliver 之前** ⇒ Deliver 退 3 时它根本不跑。只读 YAML 数
   步数是看不出来的，必须**按名字定位并断言它在末位**。
2. **`if: failure() || cancelled()`**。当初那三晚正是 `cancelled`，只写
   `failure()` 会在最需要它的形态下继续沉默。
3. **465 是隐式 TLS**，必须 `FD_ALERT_SMTP_SSL=1` + `FD_ALERT_SMTP_TLS=0`；
   同仓 `agriculture-alerts.yml` 用的是另一套字符串语义（`TLS=ssl`），
   照抄成布尔会退回 STARTTLS 连不上。

另加两条行为断言：**没有 SMTP 配置要以非零退出**（「没有通道」本身要可见，
不许静默降级成通过），**发信失败也要以非零退出**（否则红跑依旧无声）。
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
WF = REPO / ".github" / "workflows" / "health-inspect.yml"


def _steps() -> list[dict]:
    docs = list(yaml.safe_load_all(WF.read_text(encoding="utf-8")))
    return docs[0]["jobs"]["inspect"]["steps"]


def _notify_step() -> dict:
    found = [s for s in _steps() if "Notify failure" in str(s.get("name", ""))]
    assert len(found) == 1, f"失败通知步应恰好一步，实际 {len(found)}"
    return found[0]


def _heredoc(step: dict) -> str:
    m = re.search(r"<<'PYEOF'\n(.*?)\nPYEOF", step["run"], re.S)
    assert m, "通知步里没有 PYEOF heredoc"
    return m.group(1)


def test_notify_step_is_last():
    steps = _steps()
    assert steps[-1]["name"] == _notify_step()["name"], (
        "失败通知必须是最后一步：落在 Deliver 之前时，Deliver 退 3 它不跑"
    )


def test_notify_fires_on_cancelled_too():
    cond = str(_notify_step()["if"])
    assert "failure()" in cond and "cancelled()" in cond, (
        f"三晚的真实形态是 cancelled，条件必须两者都覆盖；当前为 {cond}"
    )


def test_smtp_is_implicit_tls():
    env = _notify_step()["env"]
    assert str(env.get("FD_ALERT_SMTP_SSL")) == "1", "465 端口要 SMTP_SSL=1"
    assert str(env.get("FD_ALERT_SMTP_TLS")) == "0", "开了 SSL 就必须关 STARTTLS"
    for k in ("HOST", "PORT", "USER", "PASS", "FROM", "TO"):
        assert f"FD_ALERT_SMTP_{k}" in env, f"缺 FD_ALERT_SMTP_{k}"


def test_heredoc_is_valid_python():
    compile(_heredoc(_notify_step()), "<notify>", "exec")


def _run_notify(tmp_path, extra_env: dict) -> subprocess.CompletedProcess:
    """把 heredoc 当脚本跑（cwd 换到临时目录，读不到 last-run.json）。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("FD_ALERT_SMTP_")}
    env.update(extra_env)
    env["PYTHONPATH"] = str(REPO)
    return subprocess.run([sys.executable, "-c", _heredoc(_notify_step())],
                          capture_output=True, text=True, cwd=tmp_path,
                          env=env, timeout=60, check=False)


def test_missing_smtp_config_exits_nonzero(tmp_path):
    """没有配置＝没有通道：那本身就是要被看见的状态，不许 exit 0。"""
    out = _run_notify(tmp_path, {"RUN_URL": "x", "RUN_STATE": "failure"})
    assert out.returncode == 1, f"缺 SMTP 配置却退 {out.returncode}"
    assert "没有 SMTP 配置" in out.stderr
    assert "::error::" in out.stderr, "要在 GHA 里留一条注解，不能只打日志"


def test_send_failure_exits_nonzero(tmp_path):
    """配了但发不出去（端口 1 必拒）：同样要红，否则红跑依旧无声。"""
    out = _run_notify(tmp_path, {
        "RUN_URL": "x", "RUN_STATE": "cancelled",
        "FD_ALERT_SMTP_HOST": "127.0.0.1", "FD_ALERT_SMTP_PORT": "1",
        "FD_ALERT_SMTP_FROM": "a@b.c", "FD_ALERT_SMTP_TO": "d@e.f",
        "FD_ALERT_SMTP_SSL": "1", "FD_ALERT_SMTP_TLS": "0",
    })
    assert out.returncode == 1, f"发信失败却退 {out.returncode}"
    assert "告警邮件未送出" in out.stderr
