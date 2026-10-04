#!/usr/bin/env python3
"""验证链 CLI：工单 → golden 重放 + 真实验取 + manifest 校验 + conformance gate。

这是工单 verify 字段声明的入口（萬星 spider-heal 回合内执行），也是演练脚本。
任何一环红 → 退出码非零，报告 JSON 落 stdout（含每环证据）。

生成单（source-generation-flow）适配：
- 单元未建立时返回**结构化失败**（verdict=failed + reason），不再抛异常——先建后验；
- `kind=generate` 或工单声明了非空 `golden` 时**强制 ≥1 个 golden 样本**，样本缺失判红
  （消除"无样本空洞通过"；修复链的"删样本自证"同被堵死）。

用法：
  python3 scripts/health_verify.py --ticket reports/health-tickets/<f>.yaml
  python3 scripts/health_verify.py --ticket <f> --skip-network --skip-gate   # 离线子集
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from fd_industry_data.health import guard, samples as samples_mod  # noqa: E402
from fd_industry_data.health.ticket import load_ticket, validate_ticket  # noqa: E402

SECRET_PAT = re.compile(r"(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")
_UNIT_RE = re.compile(r"^spiders/[a-z0-9][a-z0-9-]*/$")


def _quiet(fn, *args, **kwargs):
    """执行取数并吞掉其 stdout（scrapling 日志等），保持本 CLI stdout 纯 JSON。"""
    with contextlib.redirect_stdout(sys.stderr):
        return fn(*args, **kwargs)


def _resolve_unit(repo: Path, ticket: dict) -> tuple[Path | None, str | None]:
    """解析工单单元目录：形态非法/未建立分别返回结构化原因（不抛异常）。"""
    unit = str(ticket.get("unit", ""))
    if not _UNIT_RE.match(unit):
        return None, (
            f"ticket.unit 形态非法: {unit!r}（应形如 spiders/<slug>/，"
            "slug 匹配 ^[a-z0-9][a-z0-9-]*$）"
        )
    d = repo / unit
    if not d.is_dir():
        return None, (
            f"单元未建立: {unit}（生成单：先 scaffold 单元并交付 golden 样本，"
            "再跑本验证链）"
        )
    return d, None


def _manifest_entries(unit_dir: Path) -> list[str]:
    doc = yaml.safe_load((unit_dir / "manifest.yaml").read_text(encoding="utf-8")) or {}
    return [str(f.get("command", "")) for f in doc.get("functions") or [] if f.get("command")]


def _secret_scan(ticket_path: Path) -> dict:
    hits = SECRET_PAT.findall(ticket_path.read_text(encoding="utf-8"))
    return {"ok": not hits, "hits": [h[:8] + "…" for h in hits]}


def build_report(ticket_path: Path, repo: Path, limit: int = 5,
                 run_network: bool = True, run_gate: bool = True) -> dict:
    ticket = load_ticket(ticket_path)
    schema_errors = validate_ticket(ticket)
    kind = str(ticket.get("kind", "repair"))
    # 样本强制：生成单必交样本；修复单声明了 golden 就不得缺失（堵"删样本自证"）
    samples_required = kind == "generate" or bool(ticket.get("golden"))
    report: dict = {
        "ticket": str(ticket_path),
        "unit": str(ticket.get("unit", "")),
        "kind": kind,
        "samples_required": samples_required,
        "ticket_valid": not schema_errors,
        "ticket_errors": schema_errors,
        "unit_exists": False,
        "reason": "",
        "samples": [],
        "real_fetch": {"ran": False, "ok": False, "rows": 0, "detail": ""},
        "manifests": {"ran": False, "ok": False},
        "gate": {"ran": False, "ok": False},
        "secret_scan": _secret_scan(ticket_path),
    }

    unit_dir, unit_err = _resolve_unit(repo, ticket)
    if unit_err:
        report["reason"] = unit_err
        report["verdict"] = "failed"
        return report
    report["unit_exists"] = True
    report["unit"] = str(unit_dir.relative_to(repo))

    # 1) golden 重放（含样本强制）
    loaded = samples_mod.load_samples(unit_dir)
    if samples_required and not loaded:
        report["samples"].append({
            "sample_id": None,
            "ok": False,
            "diffs": ["缺少 golden 样本（本单要求 ≥1 个样本：kind=generate 或已声明 golden）"],
        })
    for sample in loaded:
        errs = samples_mod.validate_sample(sample)
        if errs:
            report["samples"].append({"sample_id": sample.get("sample_id"), "ok": False, "diffs": errs})
            continue
        if run_network:
            res = _quiet(guard.replay_sample, unit_dir, sample)
            report["samples"].append({"sample_id": sample.get("sample_id"), "ok": res.ok,
                                      "diffs": res.diffs, "rows": res.rows})
        else:
            report["samples"].append({"sample_id": sample.get("sample_id"), "ok": True,
                                      "diffs": ["skipped: --skip-network"], "rows": 0})

    # 2) 真实验取（manifest 声明入口，缺省回退到 golden 样本 target；小 limit）
    targets = [t for t in _manifest_entries(unit_dir) if t.startswith("run_")]
    if not targets:
        targets = [str(s.get("target")) for s in loaded if s.get("target")]
    targets = list(dict.fromkeys(t for t in targets if t))
    if run_network and targets:
        target = targets[0]
        try:
            entry = guard.load_unit_entry(unit_dir, target)
            try:
                rows = _quiet(entry, limit=limit)
            except TypeError:
                rows = _quiet(entry)
            rows = rows or []
            report["real_fetch"] = {"ran": True, "ok": len(rows) >= 1, "rows": len(rows),
                                    "detail": f"{target}(limit={limit})"}
        except Exception as e:  # noqa: BLE001
            report["real_fetch"] = {"ran": True, "ok": False, "rows": 0,
                                    "detail": f"{target}: {type(e).__name__}: {e}"[:300]}
    elif not targets:
        report["real_fetch"]["detail"] = "no run_* entry declared in manifest"

    # 3) manifest 校验（仓级）
    proc = subprocess.run([sys.executable, "scripts/validate_manifests.py"],
                          cwd=repo, capture_output=True, text=True, timeout=300)
    report["manifests"] = {"ran": True, "ok": proc.returncode == 0,
                           "output": (proc.stdout or proc.stderr)[-800:]}

    # 4) conformance gate（仓级）
    if run_gate:
        proc = subprocess.run([sys.executable, "scripts/conformance_gate.py", "--roots", "."],
                              cwd=repo, capture_output=True, text=True, timeout=600)
        report["gate"] = {"ran": True, "ok": proc.returncode == 0,
                          "output": (proc.stdout or proc.stderr)[-800:]}

    checks = [report["ticket_valid"], report["secret_scan"]["ok"]]
    checks += [s["ok"] for s in report["samples"]]
    if run_network and targets:
        checks.append(report["real_fetch"]["ok"])
    checks += [report["manifests"]["ok"]]
    if run_gate:
        checks.append(report["gate"]["ok"])
    report["verdict"] = "ok" if all(checks) else "failed"
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ticket", required=True)
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--skip-network", action="store_true")
    ap.add_argument("--skip-gate", action="store_true")
    args = ap.parse_args()

    report = build_report(Path(args.ticket), Path(args.repo), limit=args.limit,
                          run_network=not args.skip_network, run_gate=not args.skip_gate)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["verdict"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())