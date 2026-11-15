"""构建期内容准入闸的**接线**必须在，而不只是存在一个 `gate` 阶段。

2026-11-15 实历：`Dockerfile` 里有
`FROM base AS gate … RUN validate_manifests.py && conformance_gate.py`，
`image.yml` 顶上的注释写着「闸在每次构建时运行」——而**它一次都没跑过**：

- `image.yml` 的 build-push 只给了 `context`/`file`/`platforms`，**没有 `target`**
  ⇒ BuildKit 取最后一个阶段（`scan`）为 target；
- 没有任何阶段 `FROM gate` 或 `COPY --from=gate` ⇒ `gate` 是孤儿阶段，
  BuildKit 根本不构建它。

判定证据：同一份日志里 `[runner 2/7] …` 那些阶段行都在，`[gate` 出现 **0 次**；
而内容本身是合规的（手工跑 validate_manifests 165 manifests / 0 violations、
conformance_gate PASS 163 units / 0 violations）——所以「构建成功」从来不是
「闸通过」的证据。

这里把接线钉成断言：闸阶段必须同时跑两条命令，且必须**从默认 target 可达**
（要么 workflow 设了 `target: gate`，要么某个阶段 `COPY --from=gate`）。
两条负例保证这个测试不是在自说自话——把 copy/target 任一删掉，断言必须转红。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile"
WORKFLOW = REPO / ".github" / "workflows" / "image.yml"


def _stages(df: str) -> list[tuple[str, str]]:
    """把 Dockerfile 切成 [(阶段名, 阶段正文)]，顺序即定义顺序。

    只认 `FROM <image> AS <name>` 形态；`${BASE_IMAGE}` 这类变量不影响解析
    （阶段名在 `AS` 后面）。
    """
    out: list[tuple[str, str]] = []
    cur_name: str | None = None
    cur: list[str] = []
    for line in df.splitlines():
        m = re.match(r"(?i)^\s*FROM\s+\S+\s+AS\s+(\S+)\s*$", line)
        if m:
            if cur_name is not None:
                out.append((cur_name, "\n".join(cur)))
            cur_name = m.group(1).lower()
            cur = []
        elif cur_name is not None:
            cur.append(line)
    if cur_name is not None:
        out.append((cur_name, "\n".join(cur)))
    return out


def _gate_reachable(df: str) -> bool:
    """`gate` 阶段是否从默认 target（最后一个阶段）可达。

    可达的两种写法：某阶段 `FROM gate [AS x]`，或任一阶段有 `COPY --from=gate`
    （BuildKit 会为此把 gate 拉进构建图）。
    """
    if not _stages(df):
        return False
    if re.search(r"(?im)^\s*COPY\s+--from=gate\b", df):
        return True
    return bool(re.search(r"(?im)^\s*FROM\s+gate(\s+AS\s+\S+)?\s*$", df))


def _workflow_targets_gate(yml: str) -> bool:
    """workflow 是否显式 `target: gate`（另一条合法接线）。"""
    return bool(re.search(r"(?m)^\s*target:\s*gate\s*$", yml))


def test_gate_stage_runs_both_admission_checks():
    tags = _stages(DOCKERFILE.read_text(encoding="utf-8"))
    names = [n for n, _ in tags]
    assert "gate" in names, f"Dockerfile 里没有 gate 阶段：{names}"
    body = dict(tags)["gate"]
    assert "scripts/validate_manifests.py" in body, "gate 阶段没跑清单健全闸"
    assert "scripts/conformance_gate.py" in body, "gate 阶段没跑一致性闸"


def test_gate_stage_is_reachable_from_the_default_target():
    """这是本条缺陷的判据：阶段在 ≠ 会被构建。"""
    df = DOCKERFILE.read_text(encoding="utf-8")
    yml = WORKFLOW.read_text(encoding="utf-8")
    assert _gate_reachable(df) or _workflow_targets_gate(yml), (
        "gate 阶段从默认 target 不可达 ⇒ BuildKit 不会构建它，内容准入闸静默失效。"
        "修法：给某个阶段加 `COPY --from=gate <标记>`，或在 workflow 里设 target。"
    )


def test_reachability_check_rejects_the_orphan_shape():
    """负例：把 COPY 那一行删掉，检查函数必须判不可达（否则测的是空断言）。"""
    df = DOCKERFILE.read_text(encoding="utf-8")
    orphan = re.sub(r"(?im)^\s*COPY\s+--from=gate\b.*\n", "", df)
    assert "COPY --from=gate" not in orphan
    assert not _gate_reachable(orphan), "孤儿形态被判成可达——检查函数本身失效"
    assert not _workflow_targets_gate(
        "\n".join(l for l in WORKFLOW.read_text(encoding="utf-8").splitlines()
                  if "target: gate" not in l)
    )


def test_gate_reachable_accepts_the_from_gate_shape():
    """正例之二：`FROM gate AS …` 也算可达（另一种合法接法）。"""
    df = "FROM base AS gate\nRUN x\nFROM gate AS runner\nRUN y\n"
    assert _gate_reachable(df)


def test_gate_marker_records_the_build_time():
    """标记文件要带 UTC 时刻：它是「这一轮闸跑过」的物证，不只是空文件。"""
    body = dict(_stages(DOCKERFILE.read_text(encoding="utf-8")))["gate"]
    assert ".content-gate-ok" in body
    assert "date -u" in body, "标记里没写构建时刻，事后无法判断闸是哪一轮跑过的"
