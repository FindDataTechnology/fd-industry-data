#!/usr/bin/env python3
"""wire 需求单生成工单拉取器（wire-demand-generation-orchestration 5.x）。

从柏讯 wire 平台拉取「待投递」的 health-loop 生成工单，写入本仓
reports/health-tickets/，由调用方（GHA 工作流）提交推送后回 ack。

用法（工作流内）：
  python3 scripts/wire_demands_pull.py --repo-root . --commit-and-push

环境变量：
  WIRE_DEMANDS_URL    wire 平台基址（默认 https://wire.finddatatech.cloud）
  WIRE_DEMANDS_TOKEN  wire 内部令牌（FD_WIRE_INTERNAL_TOKEN 同值，必填）

幂等语义：
- 待投递列表为空 → 零改动退出 0；
- 工单文件已存在且内容一致 → 跳过写盘但仍回 ack（覆盖「推送成功、ack
  失败」的断点续传场景）；
- ack 失败 → 下轮重拉同单（文件内容一致 → 无 diff → 直接补 ack）。

安全：
- ticket_path 白名单硬编码 `^reports/health-tickets/....yaml$`，路径外
  一律拒（wire 侧同为白名单，双侧防御）；
- 本脚本只写工单文件，绝不 merge/写 schedule/触碰工单以外路径
  （对齐 docs/health-loop.md「权限边界（不可协商）」）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

DEFAULT_URL = "https://wire.finddatatech.cloud"
TICKET_PATH_RE = re.compile(
    r"^reports/health-tickets/[0-9]{8}-wire-[A-Za-z0-9._-]+\.yaml$"
)


def _request(base: str, token: str, method: str, path: str, body: dict | None):
    req = urllib.request.Request(
        base.rstrip("/") + path,
        method=method,
        headers={"Authorization": f"Bearer {token}"},
    )
    data = json.dumps(body).encode() if body is not None else None
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=60) as resp:
        return resp.status, json.loads(resp.read().decode() or "null")


def _git(repo: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        check=check,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--commit-and-push", action="store_true",
                    help="有变更时提交并推 main（工作流内用）")
    args = ap.parse_args()

    base = os.environ.get("WIRE_DEMANDS_URL", DEFAULT_URL)
    token = os.environ.get("WIRE_DEMANDS_TOKEN", "")
    if not token:
        print("ERROR: WIRE_DEMANDS_TOKEN required", file=sys.stderr)
        return 2

    status, pending = _request(base, token, "GET",
                               "/internal/demands/workorders/pending")
    if not isinstance(pending, list):
        print(f"unexpected pending payload: {pending!r}"[:200], file=sys.stderr)
        return 1
    if not pending:
        print("nothing pending")
        return 0

    written: list[dict] = []
    changed = False
    for item in pending:
        path = item.get("ticket_path", "")
        if not TICKET_PATH_RE.match(path):
            print(f"REFUSED non-whitelisted ticket_path: {path!r}",
                  file=sys.stderr)
            continue
        abs_path = os.path.join(args.repo_root, path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        content = item.get("ticket_yaml", "")
        if os.path.exists(abs_path) and \
                open(abs_path, encoding="utf-8").read() == content:
            written.append(item)  # 内容一致：无需改盘，但要补 ack
            continue
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        written.append(item)
        changed = True
        print(f"wrote {path}")

    if changed and args.commit_and_push:
        _git(args.repo_root, "config", "user.name", "wire-demand-puller")
        _git(args.repo_root, "config", "user.email",
             "wire-demand-puller@finddatatech.cloud")
        _git(args.repo_root, "add", "reports/health-tickets")
        if _git(args.repo_root, "diff", "--cached", "--quiet", check=False).returncode == 0:
            print("no staged changes")
        else:
            _git(args.repo_root, "commit", "-m",
                 "wire: demand workorders " +
                 ", ".join(i.get("code", "?") for i in written))
            ok = False
            for attempt in range(1, 4):
                if _git(args.repo_root, "push", "origin", "HEAD:main",
                        check=False).returncode == 0:
                    ok = True
                    break
                print(f"push attempt {attempt} failed; rebase+retry")
                _git(args.repo_root, "pull", "--rebase", "origin", "main",
                     check=False)
            if not ok:
                print("push failed after retries; not acking", file=sys.stderr)
                return 1

    acked = 0
    for item in written:
        st, _ = _request(base, token, "POST",
                         "/internal/demands/workorders/ack",
                         {"code": item.get("code"),
                          "ticket_path": item.get("ticket_path"),
                          "by": "fleet-puller"})
        if st == 200:
            acked += 1
        else:
            print(f"ack failed for {item.get('code')}: HTTP {st}",
                  file=sys.stderr)
    print(f"done: {len(written)} ticket(s), {acked} acked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
