"""告警脚本 ↔ 部署镜像的符号契约：脚本从 ConfigMap 来，包从镜像来。

2026-11-14 我自己造过一次生产故障：给三条告警脚本加了
`from fd_industry_data.alerts_core import state_health`，本地测试全绿（venv 里有新包），
但线上脚本是 ConfigMap 挂进 `/src`、`fd_industry_data` 来自镜像
（`/usr/local/lib/python3.12/site-packages/fd_industry_data`），而镜像

    kubectl -n fd-prod run state-probe --rm -i \
      --image=ccr.ccs.tencentyun.com/finddata/fd-industry-runner:sha-f5a19aa \
      -- python3 -c "import fd_industry_data.alerts_core as m; print(hasattr(m,'state_health'))"
    → has state_health: False

没有那个符号 ⇒ 下一轮三条线全部 `ImportError`，**一封告警都不发**——正好在
cheap-5 掉线的当天把告警关掉。当场回退内联副本（`fd-infra-deploy` `fa9a32d`）才止血。

所以这条契约必须显式写下来：**脚本能 import 的名字，只能是镜像里已经有的**。
新增符号有两条路——重出镜像，或者把逻辑写进脚本自身（今天的做法：`_state_info`
只用镜像确有的 `load_state`）。

`IMAGE_EXPORTS` 是**实测快照**，不是抄文档：
- 取法：在 fd-prod 里用该镜像 `--rm` 跑 `python3 -c "import fd_industry_data.alerts_core
  as m; print(sorted(n for n in dir(m) if not n.startswith('_')))"`；
- 日期：2026-11-14；镜像 tag：`fd-industry-runner:sha-f5a19aa`（四条告警线同一个）。
镜像换代后这份表要**重新实测**再改，别照抄 code——它就是「线上到底认哪些名字」。
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

#: 由镜像提供的符号，**按模块分表**（实测快照，见模块 docstring）。
#:
#: 分表是必须的：第一版拿顶层 `alerts_core` 的导出表去比
#: `from ...alerts_core.delivery import judge`，于是把**一直就在镜像里**的
#: `judge`/`DeliveryReceipt` 报成缺失——假红的闸等于没有闸。
IMAGE_EXPORTS: dict[str, frozenset[str]] = {
    "fd_industry_data.alerts_core": frozenset({
        "FIRING", "FIRING_V", "INSUFFICIENT", "OK", "PENDING", "QUIET", "RESOLVED",
        "RuleState", "SmtpConfig", "append_events", "attach_rule", "compare",
        "dispatch", "evaluate_event", "evaluate_threshold", "event_key", "load_state",
        "make_event", "read_events", "save_state", "send_email", "smtp_config_from_env",
        # 子模块与 `from __future__ import annotations`
        "delivery", "ledger", "notify", "state", "annotations",
    }),
    "fd_industry_data.alerts_core.delivery": frozenset({
        "CHANNELS", "DeliveryNotConfigured", "DeliveryReceipt", "DeliveryTarget",
        "RunVerdict", "STRICT_ENV", "count_firing", "judge", "resolve_target",
        "smtp_from_env", "strict_enabled", "annotations",
    }),
    "fd_industry_data.alerts_core.ledger": frozenset({
        "Path", "annotations", "append_events", "event_key", "load_state",
        "read_events", "save_state",
    }),
    "fd_industry_data.alerts_core.notify": frozenset({
        "ENV_KEYS", "EmailMessage", "SmtpConfig", "annotations", "dispatch",
        "send_email", "smtp_config_from_env",
    }),
    "fd_industry_data.alerts_core.state": frozenset({
        "FIRING", "FIRING_V", "INSUFFICIENT", "OK", "PENDING", "QUIET", "RESOLVED",
        "RuleState", "annotations", "attach_rule", "compare", "evaluate_event",
        "evaluate_threshold", "make_event",
    }),
}

#: 跑在那个镜像里、由 ConfigMap 供给的脚本。**只列这四个**：其余脚本走
#: `git clone` / checkout 供给（如 health_inspect.py 由 workflow 现拉仓），
#: 不依赖镜像里的包版本，算进来只会得到一条与事实无关的红。
DEPLOYED_VIA_CONFIGMAP = (
    "node_health_alerts.py",
    "ciyuan_line_alerts.py",
    "plaza_freshness_alerts.py",
    "shard_health_alerts.py",
)


def _imported_names(path: pathlib.Path) -> dict[str, list[str]]:
    """返回 ``{模块: [名字…]}``，只看 `from fd_industry_data… import …`。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("fd_industry_data"):
            out.setdefault(node.module or "", []).extend(a.name for a in node.names)
    return out


@pytest.mark.parametrize("script", DEPLOYED_VIA_CONFIGMAP)
def test_script_only_imports_symbols_the_image_has(script):
    p = REPO / "scripts" / script
    if not p.exists():
        pytest.skip(f"{script} 不在仓里（尚无真源）")
    imported = _imported_names(p)
    missing = {}
    for mod, names in imported.items():
        known = IMAGE_EXPORTS.get(mod)
        if known is None:          # 没实测过的模块：判不出就点名，不当作通过
            missing[mod] = [f"（未见实测导出表：{mod}）"]
            continue
        bad = [n for n in names if n not in known]
        if bad:
            missing[mod] = bad
    assert not missing, (
        f"{script} 从 {sorted(missing)} 导入了镜像里没有的符号 {missing}。\n"
        "脚本由 ConfigMap 挂进 /src、包来自镜像 ⇒ 线上会 ImportError，整条告警线静默变哑。\n"
        "两条路：① 重出 fd-industry-runner 镜像并同步 IMAGE_EXPORTS；"
        "② 把逻辑写进脚本自身，只用镜像确有的符号（参考 _state_info）。"
    )


def test_state_info_is_script_local_not_an_image_symbol():
    """回归今天那次故障本身：脚本**不得**再从包里取 `state_health`。

    不是不许有这个能力，而是不许以「包里新增符号」的方式获得它——那要等镜像。
    """
    for script in DEPLOYED_VIA_CONFIGMAP:
        p = REPO / "scripts" / script
        if not p.exists():
            continue
        names = [n for v in _imported_names(p).values() for n in v]
        assert "state_health" not in names, (
            f"{script} 又 import 了镜像里没有的 state_health；2026-11-14 就是这样让三条线"
            " ImportError 的。请用脚本内的 _state_info。"
        )
        assert "state_info = _state_info(STATE)" in p.read_text(encoding="utf-8"), (
            f"{script} 没接本轮状态读数"
        )


def test_every_deployed_script_exists():
    """四个都必须有真源——「只在清单里」的那份没有任何东西能审、能 diff、能回溯。"""
    missing = [s for s in DEPLOYED_VIA_CONFIGMAP if not (REPO / "scripts" / s).exists()]
    assert not missing, f"这些告警脚本在仓里没有真源（只活在 ConfigMap 内联段里）：{missing}"


# ── 本地 helper 与库实现的逐档对齐（两份实现，必须同答） ─────────────────────
@pytest.fixture
def helper_of():
    """取脚本里的 `_state_info`：import 脚本模块，拿函数本体。"""
    import importlib
    cache: dict[str, object] = {}

    def get(script: str):
        if script not in cache:
            mod = importlib.import_module(f"scripts.{script.replace('.py', '')}")
            cache[script] = mod._state_info
        return cache[script]

    return get


@pytest.mark.parametrize("script", ["node_health_alerts", "ciyuan_line_alerts",
                                    "plaza_freshness_alerts", "shard_health_alerts"])
def test_local_helper_agrees_with_library(script, helper_of, tmp_path):
    """脚本里的 `_state_info` 与 `alerts_core.ledger.state_health` 必须逐档同答。

    刻意保留两份实现：脚本那份不能依赖镜像里的新符号（见模块 docstring），
    库那份是给镜像更新后用的规范版。两份各改各的就会分叉，所以在这里对齐：
    空目录 / 已有状态 / 坏 JSON / 不可写目录 四档。
    """
    import json
    import os

    from fd_industry_data.alerts_core import ledger

    local = helper_of(script)

    def both(p):
        return local(p), ledger.state_health(p)

    d = tmp_path / "state"
    a, b = both(d / "alert-state.json")
    assert a == b, "空目录一档不同答"

    (d).mkdir(parents=True, exist_ok=True)
    (d / "alert-state.json").write_text(json.dumps({"r": {"state": "firing"}}), encoding="utf-8")
    a, b = both(d / "alert-state.json")
    assert a == b, "已有状态一档不同答"

    (d / "alert-state.json").write_text("{bad json", encoding="utf-8")
    a, b = both(d / "alert-state.json")
    assert a == b, "坏 JSON 一档不同答"

    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, 0o500)
    try:
        a, b = both(ro / "alert-state.json")
    finally:
        os.chmod(ro, 0o700)
    assert a == b, "不可写一档不同答"


@pytest.mark.parametrize("script", ["node_health_alerts", "ciyuan_line_alerts",
                                    "plaza_freshness_alerts", "shard_health_alerts"])
def test_helper_is_identical_across_the_four_scripts(script):
    """四份 helper 必须逐字相同——同一段逻辑抄四遍，只允许一种版本。"""
    def grab(name: str) -> str:
        s = (REPO / "scripts" / f"{name}.py").read_text(encoding="utf-8").splitlines()
        i = next(k for k, l in enumerate(s) if l.startswith("def _state_info"))
        j = next(k for k in range(i + 1, len(s))
                 if s[k].startswith("def ") or (s[k] and not s[k].startswith((" ", "#"))))
        return "\n".join(s[i:j]).strip()

    canonical = grab("node_health_alerts")
    assert grab(script) == canonical, (
        f"{script} 里的 _state_info 与 node_health_alerts 的不一致；"
        "同一读数四处各写一遍就会各自漂移"
    )
