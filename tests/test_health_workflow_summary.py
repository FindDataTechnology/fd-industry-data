"""巡检摘要的截断窗口：别把「三源窗口态」切掉。

为什么单独立一条
----------------
观察步骤把 `min_rows_observation.py` 的输出截前 N 行贴进 step summary
（`sed -n '1,24p'`）。而「三源窗口态」是这段输出**最后**才打印的——只要上面
再长出一段（多一个指纹、多一行提示），它就会滑出截断窗口。

后果不是"少看一行"，是**看摘要的人会以为没有源未恢复**：摘要里根本不存在那三行，
既看不到 `✗no_new_job` 也看不到"窗口未到"，会被默认读成正常。

所以这里把"N 是多少"从 workflow 里读出来，再验那三行确实落在 N 之内——
将来谁把截断调小、或往前面加段落，这条会红。
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "health-inspect.yml"
SCRIPT = REPO / "scripts" / "min_rows_observation.py"
STEP_NAME = "观察表追加"


def _obs_step() -> dict:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for s in doc["jobs"]["inspect"]["steps"]:
        if STEP_NAME in (s.get("name") or ""):
            return s
    raise AssertionError(f"workflow 里找不到含「{STEP_NAME}」的步骤")


def summary_cap() -> int:
    """摘要里截取 obs.log 的行数上限（从 workflow 的 sed 里读，不写死）。"""
    run = _obs_step()["run"]
    m = re.search(r"sed -n '1,(\d+)p'\s+/tmp/obs\.log", run)
    if m is None:
        raise AssertionError("workflow 里找不到摘要的截断命令（sed -n '1,Np' /tmp/obs.log）")
    return int(m.group(1))


def observation_output() -> list[str]:
    """用一份最小遥测快照跑观察脚本，拿它的真实输出行。"""
    with tempfile.TemporaryDirectory(prefix="obs-cap-") as td:
        snap = Path(td) / "tel.json"
        snap.write_text(
            '[{"source":"cisa","status":"success","rows":100,'
            '"started_at":"2026-11-09T00:00:00+00:00"}]', encoding="utf-8")
        out = subprocess.run(
            [sys.executable, str(SCRIPT), "--snapshot", str(snap),
             "--day", "2026-11-10", "--now", "2026-11-10T12:00:00+08:00"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, cwd=str(REPO),
        )
    return out.stdout.splitlines()


class SummaryCapTests(unittest.TestCase):
    def test_window_block_within_cap(self):
        cap = summary_cap()
        lines = observation_output()
        idx = next((i for i, l in enumerate(lines, 1) if "三源窗口态" in l), None)
        self.assertIsNotNone(idx, "输出里没有「三源窗口态」段——判据本身丢了")
        self.assertLessEqual(
            idx, cap,
            f"「三源窗口态」起于第 {idx} 行，超出摘要截断 {cap} 行："
            f"摘要里将看不到它，读的人会以为没有源未恢复。"
            f"（要么把 sed 的上限调大，要么把上面的段落收短）")

    def test_three_rows_follow_the_header(self):
        """表头之后必须恰好三行（三源），否则摘要里那一段会不完整。"""
        lines = observation_output()
        idx = next(i for i, l in enumerate(lines) if "三源窗口态" in l)
        tail = [l for l in lines[idx + 1:] if l.strip()]
        self.assertEqual(len(tail), 3, f"表头后应有 3 行（三源），实得 {len(tail)} 行")
        for name in ("gb-standards", "cisa", "boxofficemojo"):
            self.assertTrue(any(name in l for l in tail), f"三行里缺 {name}")

    def test_cap_is_large_enough_for_whole_output(self):
        """现状：整段输出也就十几行，截断上限不该小于实际输出。"""
        lines = observation_output()
        self.assertGreaterEqual(summary_cap(), len(lines),
                                f"截断 {summary_cap()} < 实际输出 {len(lines)} 行，尾巴会被切掉")


if __name__ == "__main__":
    unittest.main()
