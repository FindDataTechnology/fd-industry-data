"""CaaS 合规闸的测试（openspec: add-caas-delivery-contract 3.1/3.2）。

**这个文件测的是闸本身**，不是被测系统。一个只会绿的闸等于没有闸——2026-11-17
的教训是「门从未执行，而输出和『执行且通过』一模一样」。所以这里既跑正向（当前
仓库应 PASS），也跑负向（喂坏样本必须 RED），后者是闸有效的唯一证据。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_caas_conformance.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_caas_conformance", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # dataclasses 解析 field 的默认值时按 __module__ 回查 sys.modules；不注册会 AttributeError。
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load()


def test_repo_is_currently_conformant(gate):
    """正向：当前仓库必须 PASS。失败说明契约被违反了，不是闸坏了。"""
    report = gate.run()
    failed = report.as_dict()["failed"]
    assert report.ok, f"仓库当前不合规：{failed}"


def test_self_test_passes(gate):
    """闸自检：四条判据都必须能判红，合规样本判绿。"""
    assert gate.self_test() == 0


def test_copy_drift_is_detected(gate, tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"version": 1, "forms": {k: {} for k in ("text", "chart", "file", "asset", "link")}}))
    copy = tmp_path / "copy.json"
    copy.write_text(json.dumps({"version": 1, "forms": {"text": {}}}))
    r = gate.Report()
    gate.check_authority(r, authority=auth, copies={"drifted": copy})
    assert not r.ok


def test_hardcoded_form_list_is_detected(gate, tmp_path):
    # JS 字符串数组
    js = tmp_path / "impl.js"
    js.write_text('const FORMS = ["text", "chart", "file"];\n')
    r = gate.Report()
    gate.check_no_parallel_form_list(r, scan={"impl.js": js})
    assert not r.ok
    # Python 元组
    py = tmp_path / "impl.py"
    py.write_text('FORMS = ("text", "chart", "file", "asset", "link")\n')
    r = gate.Report()
    gate.check_no_parallel_form_list(r, scan={"impl.py": py})
    assert not r.ok


def test_reading_from_authority_is_not_flagged(gate, tmp_path):
    good = tmp_path / "impl.js"
    good.write_text('import { FORMS } from "./caas-forms.json";\n// delivered consumed failed\n'
                    'export function idemKeyFor() {}\n')
    r = gate.Report()
    gate.check_no_parallel_form_list(r, scan={"impl.js": good})
    assert r.ok


def test_missing_ack_three_values_is_detected(gate, tmp_path):
    impl = tmp_path / "impl.py"
    impl.write_text("state = 'sent'\n")
    r = gate.Report()
    gate.check_ack_projection(r, impls={"impl.py": impl})
    assert not r.ok


def test_missing_idempotency_is_detected(gate, tmp_path):
    impl = tmp_path / "impl.py"
    impl.write_text("def deliver(): pass\n")
    r = gate.Report()
    gate.check_idempotency(r, impls={"impl.py": (impl, "idemKeyFor")})
    assert not r.ok


def test_json_output_shape(gate):
    payload = gate.run().as_dict()
    assert set(payload) == {"ok", "partial", "uncovered", "findings", "failed"}
    assert isinstance(payload["findings"], list)
