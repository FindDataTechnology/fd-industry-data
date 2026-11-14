"""删掉「已完成但没人清」的 Pod（2026-11-14 立）。

**为什么需要**：完成态 Pod 不提供服务，却仍占两样东西——宿主的**镜像层与容器日志**
（今天 xinru-server1 就是被 241 个完成态钉住镜像、让 kubelet 的镜像 GC
`freed 0 bytes`，最后 pause 镜像被清掉、那台机起不了任何新 Pod），以及命名空间的
**配额**（实测：fd-prod 删掉 75 个完成态后 `limits.cpu` 28070m→27070m）。
k8s 自带的 `terminated-pod-gc-threshold` 缺省 12500：在 38 GiB 级别的镜像盘上，
等它动手时盘早满了。

**删什么（三条同时成立）**：
  1. 相位是 `Succeeded` 或 `Failed`（Running / Pending 一律不碰）；
  2. 年龄 ≥ `POD_GC_MIN_AGE_H`（缺省 1.0 小时）——刚结束的先留着，给当场排查用；
  3. 有 `Job` 或 `ReplicaSet` 控制器 owner。
     裸 Pod（没有控制器）**永不删**：它可能是有人手工起的现场，删了不可恢复。

**为什么不伤排查**：Job/ReplicaSet 对象本身保留（`failedJobsHistoryLimit` 管的是它们），
`check_cronjob_health.py` 读的是 Job 的 condition 与 `lastSuccessfulTime`，不读 Pod；
失败 Pod 的日志本来就常被裁掉（`failedJobsHistoryLimit` 保 Job、Pod 被清）。
所以删 Pod ≈ 删残骸，不动证据。

用法：
    python3 scripts/pod_gc.py --dry-run            # 只列出将删的
    python3 scripts/pod_gc.py                      # 真删（在 Pod 内跑）
参数：`POD_GC_NAMESPACE`（缺省取 SA 所在 ns，兜底 scraw）、`POD_GC_MIN_AGE_H`（缺省 1.0，
设 0 只用于自证能红）、`POD_GC_API`（缺省由 KUBERNETES_SERVICE_HOST 拼）。

判据不在这个进程里：容器内 `disk_usage("/")` 量的是它自己的 rootfs，不是宿主镜像盘，
打印出来是假读数。真判据是「节点事件不再有 ImageGCFailed / FailedCreatePodSandBox」
与「新 Pod 能起来」。
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
DEFAULT_NS = "scraw"
DEFAULT_MIN_AGE_H = 1.0
DELETABLE_PHASES = ("Succeeded", "Failed")
DELETABLE_OWNER_KINDS = ("Job", "ReplicaSet")


def parse_ts(raw: str | None) -> datetime | None:
    """解析 RFC3339（k8s 给的形状）。解析不出返回 None——调用方据此**不删**。"""
    if not raw:
        return None
    text = raw.strip()
    # Python 3.9/3.10 的 fromisoformat 不认 'Z'，只认 '+00:00'。
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # 纳秒截断到微秒
    if "." in text:
        head, rest = text.split(".", 1)
        frac = ""
        for ch in rest:
            if ch.isdigit():
                frac += ch
            else:
                break
        tail = rest[len(frac):]
        text = f"{head}.{frac[:6]}{tail}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def owned_by_controller(pod: dict) -> bool:
    for ref in (pod.get("metadata") or {}).get("ownerReferences") or []:
        if ref.get("kind") in DELETABLE_OWNER_KINDS:
            return True
    return False


def select_pods(pods: list[dict], now: datetime, min_age_h: float) -> tuple[list[str], int, int]:
    """纯选择：返回 (要删的名字, 跳过的完成态数, 解析失败数)。

    跳过计数让「什么都没删」与「全都太新/无 owner」在日志里可分辨——
    否则一次判据写错会伪装成「本来就没东西可删」。
    """
    dead: list[str] = []
    skipped = 0
    unparsed = 0
    for pod in pods:
        status = pod.get("status") or {}
        if status.get("phase") not in DELETABLE_PHASES:
            continue
        if not owned_by_controller(pod):
            skipped += 1
            continue
        stamp = status.get("startTime") or (pod.get("metadata") or {}).get("creationTimestamp")
        ts = parse_ts(stamp)
        if ts is None:
            # 时间读不出就不删：一次时间格式变更不该变成「删光」。
            unparsed += 1
            continue
        if (now - ts).total_seconds() / 3600.0 >= min_age_h:
            dead.append((pod.get("metadata") or {}).get("name") or "")
        else:
            skipped += 1
    return [n for n in dead if n], skipped, unparsed


def _api_call(path: str, method: str = "GET", token: str = "", ctx=None):
    api = os.environ.get("POD_GC_API") or "https://%s:%s" % (
        os.environ.get("KUBERNETES_SERVICE_HOST", ""),
        os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443"))
    req = urllib.request.Request(api + path, method=method,
                                 headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, context=ctx, timeout=30) as r:  # noqa: S310
        body = r.read().decode() or "{}"
        return json.loads(body)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="清理完成态 Pod（完成态仍占镜像层与配额）")
    ap.add_argument("--namespace", default=os.environ.get("POD_GC_NAMESPACE") or DEFAULT_NS)
    ap.add_argument("--min-age-hours", type=float,
                    default=float(os.environ.get("POD_GC_MIN_AGE_H") or DEFAULT_MIN_AGE_H))
    ap.add_argument("--dry-run", action="store_true", help="只列出，不删")
    ap.add_argument("--from-json", help="离线快照：{items:[pod…]}；缺省真打 API")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)

    if args.from_json:
        with open(args.from_json, encoding="utf-8") as fh:
            pods = (json.load(fh) or {}).get("items") or []
        token, ctx = "", None
    else:
        token = open(f"{SA_DIR}/token", encoding="utf-8").read().strip()
        ctx = ssl.create_default_context(cafile=f"{SA_DIR}/ca.crt")
        pods = _api_call(f"/api/v1/namespaces/{args.namespace}/pods", token=token, ctx=ctx).get("items") or []

    dead, skipped, unparsed = select_pods(pods, now, args.min_age_hours)

    deleted, failed = 0, []
    if not args.dry_run:
        for name in dead:
            try:
                _api_call(f"/api/v1/namespaces/{args.namespace}/pods/{name}",
                          method="DELETE", token=token, ctx=ctx)
                deleted += 1
            except Exception as exc:  # noqa: BLE001 - 单条失败不该中断整批
                failed.append(f"{name}: {type(exc).__name__} {exc}")
    else:
        deleted = len(dead)

    print(json.dumps({
        "namespace": args.namespace,
        "min_age_hours": args.min_age_hours,
        "candidates": len(dead),
        "deleted": deleted,
        "dry_run": bool(args.dry_run),
        "skipped_finished_but_kept": skipped,
        "unparsed_timestamps": unparsed,
        "failed": failed[:5],
        "sample_deleted": dead[:5],
    }, ensure_ascii=False))
    # 有删除目标就 0（清理是常态）；只有「一条都没检查成」才非 0。
    return 0 if (pods or args.from_json) else 2


if __name__ == "__main__":
    sys.exit(main())
