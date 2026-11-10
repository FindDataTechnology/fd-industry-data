"""health-inspect.yml 的退出码分支：拿 YAML 里的**原文**跑，不另抄一份。

为什么这么测
------------
2026-11-11 修过一个"报错说错地方"的缺陷：观察脚本有三种退出码
（0 正常 / 2 没遥测已拒绝写表 / 3 表已写入但有源未恢复），而 workflow 里
一律报「观察表追加失败（rc=$rc）」。于是 rc=3 会被读成写盘出问题——那时行
其实写成功了，真问题在源侧。

这类文案很容易在后续改动里退化（有人加一个退出码、或把 case 改回 if），
而 `set +e` + `exit 0` 让它在 CI 上永远绿。所以把 workflow 里那段 `case`
**原样抽出来执行**，按 rc 断言输出——测的是真产物，不是复制品。
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "health-inspect.yml"
STEP_NAME = "观察表追加"


def _step() -> dict:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for s in doc["jobs"]["inspect"]["steps"]:
        if STEP_NAME in (s.get("name") or ""):
            return s
    raise AssertionError(f"workflow 里找不到含「{STEP_NAME}」的步骤")


def _case_block() -> str:
    """从步骤的 run 文本里抽出 `case "$rc" in … esac` 整段。"""
    run = _step()["run"]
    i = run.index('case "$rc" in')
    j = run.index("esac") + len("esac")
    return run[i:j]


def message_for(rc: int) -> str:
    """把抽出的 case 块交给 bash 执行，返回它会打印的那一行（无输出则空串）。"""
    out = subprocess.run(
        ["bash", "-c", f'rc={rc}\n{_case_block()}'],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    return out.stdout.strip()


class ExitCodeBranchTests(unittest.TestCase):
    def test_case_block_exists(self):
        """必须是 case 分支。退回单个 if 就等于又把三种码揉成一句话。"""
        self.assertIn('case "$rc" in', _step()["run"])
        self.assertGreater(len(_case_block()), 0)

    def test_rc0_is_silent(self):
        self.assertEqual(message_for(0), "", "正常退出不该产生任何 warning")

    def test_rc2_says_not_written(self):
        msg = message_for(2)
        self.assertIn("::warning::", msg)
        self.assertIn("未写入", msg)
        self.assertIn("遥测", msg)
        self.assertNotIn("追加失败", msg, "rc=2 是主动拒绝写，不是写失败")

    def test_rc3_says_written_but_source_unrecovered(self):
        msg = message_for(3)
        self.assertIn("::warning::", msg)
        self.assertIn("已写入", msg)
        self.assertIn("未恢复", msg)
        self.assertIn("§11", msg, "rc=3 要指向核 Job 的步骤，否则又被送去调试 append")
        self.assertNotIn("追加失败", msg, "这条正是 2026-11-11 修掉的误报文案")

    def test_unknown_rc_is_generic(self):
        msg = message_for(9)
        self.assertIn("::warning::", msg)
        self.assertIn("9", msg, "未知码要把实际值带出来")
        self.assertIn("异常", msg)

    def test_three_codes_are_distinct(self):
        msgs = {message_for(rc) for rc in (2, 3, 9)}
        self.assertEqual(len(msgs), 3, "三种非零码必须是三句不同的话，否则分支没起作用")

    def test_step_is_still_non_blocking(self):
        """回归前提：该步不阻断（末尾 exit 0）。若哪天改成阻断，判定树的语义就变了。"""
        lines = [l.strip() for l in _step()["run"].strip().splitlines() if l.strip()]
        self.assertEqual(lines[-1], "exit 0")


class ScriptExitCodeContract(unittest.TestCase):
    """脚本侧的退出码必须与 workflow 认的那三个值对齐。"""

    def setUp(self):
        self.src = (Path(__file__).resolve().parents[1] / "scripts"
                    / "min_rows_observation.py").read_text(encoding="utf-8")

    def test_script_uses_only_documented_codes(self):
        self.assertIn("return 2", self.src, "无遥测时拒绝写表要返回 2")
        self.assertIn("return 3 if any", self.src, "有源未恢复要返回 3")
        self.assertIn("else 0", self.src, "否则返回 0（尾部是三元表达式，不是字面 return 0）")


if __name__ == "__main__":
    unittest.main()
