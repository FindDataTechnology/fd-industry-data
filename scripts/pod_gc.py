"""删掉「已完成但没人清」的 Pod（2026-11-14 立）。

**为什么需要**：完成态 Pod 不提供服务，却仍占两样东西——宿主的**镜像层与容器日志**
（今天 xinru-server1 就是被 241 个完成态钉住镜像、让 kubelet 的镜像 GC
`freed 0 bytes`，最后 pause 镜像被清掉、那台机起不了任何新 Pod），以及命名空间的
**配额**（实测：fd-prod 删掉 75 个完成态后 `limits.cpu` 28070m→27070m）。
k8s 自带的 `terminated-pod-gc-threshold` 缺省 12500：在 38 GiB 级别的镜像盘上，
等它动手时盘早满了。

**删什么（四条同时成立）**：
  1. 相位是 `Succeeded` 或 `Failed`（Running / Pending 一律不碰）；
  2. 年龄 ≥ `POD_GC_MIN_AGE_H`（缺省 1.0 小时）——刚结束的先留着，给当场排查用；
  3. 有 `Job` 或 `ReplicaSet` 控制器 owner。
     裸 Pod（没有控制器）**永不删**：它可能是有人手工起的现场，删了不可恢复；
  4. **不是失败 Job 的 Pod**（见下一条）——否则不删。

**「删 Pod ≈ 删残骸，不动证据」是错的（2026-11-15 实历，第一版就是这么写的）**：
`fd-prod/sink-freshness` 的排期轮在 11-14T21:10Z 失败（`BackoffLimitExceeded`），
两个失败 Pod 在 22:11Z 就被这条作业清了，等要看「为什么失败」时只剩一行 condition，
日志没了。`failedJobsHistoryLimit` 保的是 Job 对象，**保不住 Pod 日志**；而失败原因
只在最后一个 Pod 的日志里。所以现在：owner Job 带 `Failed=True` 的 Pod 进**封存档**，
留 `POD_GC_FORENSIC_H`（缺省 72 小时）再删。数量有界——每个 CronJob 至多
`failedJobsHistoryLimit × (backoffLimit+1)` 个，失败本来就是少数。
取不到 Job 表时（离线、API 被拒）退回 Pod 自己的 `terminated.exitCode != 0` 判，
并在输出里标 `jobs_map: unavailable`，让「封存档没生效」这件事可见。

用法：
    python3 scripts/pod_gc.py --dry-run            # 只列出将删的
    python3 scripts/pod_gc.py                      # 真删（在 Pod 内跑）
参数：`POD_GC_NAMESPACE`（缺省取 SA 所在 ns，兜底 scraw）、`POD_GC_MIN_AGE_H`（缺省 1.0，
设 0 只用于自证能红）、`POD_GC_FORENSIC_H`（缺省 72.0，设 0 只用于自证「封存到期会删」）、
`POD_GC_API`（缺省由 KUBERNETES_SERVICE_HOST 拼）。

判据不在这个进程里：容器内 `disk_usage("/")` 量的是它自己的 rootfs，不是宿主镜像盘，
打印出来是假读数。真判据是「节点事件不再有 ImageGCFailed / FailedCreatePodSandBox」
与「新 Pod 能起来」。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
DEFAULT_NS = "scraw"
DEFAULT_MIN_AGE_H = 1.0
DEFAULT_FORENSIC_H = 72.0
DEFAULT_FORENSIC_MAX = 2
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


def owner_job_name(pod: dict) -> str | None:
    for ref in (pod.get("metadata") or {}).get("ownerReferences") or []:
        if ref.get("kind") == "Job":
            return ref.get("name")
    return None


def failed_job_names(jobs: list[dict]) -> set[str]:
    """Job 表里 `Failed=True` 的那些名字。

    只认 condition，不认 `status.failed > 0`：重试过但最终成功的 Job 也有 failed 计数，
    而它的失败 Pod 是残骸；永久失败的 Job 才是「日志是唯一证据」的那种。
    """
    out: set[str] = set()
    for job in jobs:
        name = (job.get("metadata") or {}).get("name")
        if not name:
            continue
        for cond in ((job.get("status") or {}).get("conditions") or []):
            if cond.get("type") == "Failed" and str(cond.get("status")) == "True":
                out.add(name)
                break
    return out


def pod_exit_code(pod: dict) -> int | None:
    """Pod 自己最后一次容器退出码；读不到返回 None（不据此判失败）。"""
    for cs in ((pod.get("status") or {}).get("containerStatuses") or []):
        term = ((cs.get("state") or {}).get("terminated") or {})
        if "exitCode" in term:
            return term["exitCode"]
    return None


def unavailable_note(exc: BaseException) -> str:
    """`unavailable: HTTPError:403` —— 状态码必须带出来。

    **只写异常类名会让三种完全不同的故障长得一模一样**（2026-11-15 实历）：
    403 是 Role 少一条 `batch/jobs`（改 RBAC）、404 是 API 路径/版本不对（改代码）、
    401/超时是凭据/网络。当天 `sink-freshness` 那个失败 Job 正好撞上 Role 修复的
    提交窗口（Job 02:11:00Z 起跑，Role 02:11:34Z 才提交、02:19:54Z 才同步），
    日志里只有 `HTTPError` 四个字——不查一遍 RBAC 根本分不出是「还在等同步」
    还是「压根没授上」。同族于「权限缺口与功能缺失同形」。
    """
    code = getattr(exc, "code", None)
    return f"unavailable: {type(exc).__name__}" + (f":{code}" if code else "")


def select_pods(pods: list[dict], now: datetime, min_age_h: float,
                failed_jobs: set[str] | None = None,
                forensic_h: float = DEFAULT_FORENSIC_H,
                forensic_max: int = DEFAULT_FORENSIC_MAX) -> tuple[list[str], int, int, int]:
    """纯选择：返回 (要删的名字, 跳过的完成态数, 解析失败数, 封存留观的数)。

    跳过计数让「什么都没删」与「全都太新/无 owner」在日志里可分辨——
    否则一次判据写错会伪装成「本来就没东西可删」。封存计数同理：少了它，
    「没有失败作业」与「封存档写反成永不留」长得一模一样。

    `failed_jobs=None` 表示 Job 表取不到：退回 Pod 自己的退出码判失败，
    调用方要在输出里标明这一点（`jobs_map: unavailable`）。

    **封存有上限（`forensic_max`，每个 CronJob 只留最新 N 个）**：有些作业的失败是
    **设计内常红** —— `node-debugger-privileged/image-gc-vm-0-4-ubuntu` 每 15 分钟按
    82% escalate 门判一次失败，留 72h 就是 ~288 个 Pod 全钉着镜像，而那条线正是为了
    「盘紧时给 kubelet 的镜像 GC 松绑」才存在的（2026-11-15 自查当场看到 2 个）。
    证据的价值在**最近一次失败**的日志里，不在第 288 份副本。
    """
    dead: list[str] = []
    skipped = 0
    unparsed = 0
    held = 0
    forensic_pods: list[tuple[datetime, str, str]] = []  # (时间, Pod 名, owner Job 名)
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
        age_h = (now - ts).total_seconds() / 3600.0
        if age_h < min_age_h:
            skipped += 1
            continue
        if failed_jobs is None:
            forensic = (pod_exit_code(pod) or 0) != 0
        else:
            job = owner_job_name(pod)
            forensic = job is not None and job in failed_jobs
        name = (pod.get("metadata") or {}).get("name") or ""
        if forensic and age_h < forensic_h:
            forensic_pods.append((ts, name, owner_job_name(pod) or name))
            continue
        dead.append(name)

    # 封存限额：按 owner Job 去掉尾部的 `-<epoch分钟>` 归到 CronJob，每个前缀只留最新 N 个。
    forensic_pods.sort(key=lambda item: item[0], reverse=True)
    seen: dict[str, int] = {}
    for _ts, name, job in forensic_pods:
        prefix = re.sub(r"-\d{5,}$", "", job)
        seen[prefix] = seen.get(prefix, 0) + 1
        if seen[prefix] <= max(0, int(forensic_max)):
            held += 1
        else:
            dead.append(name)
    return [n for n in dead if n], skipped, unparsed, held


def _api_call(path: str, method: str = "GET", token: str = "", ctx=None):
    api = os.environ.get("POD_GC_API") or "https://%s:%s" % (
        os.environ.get("KUBERNETES_SERVICE_HOST", ""),
        os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443"))
    req = urllib.request.Request(api + path, method=method,
                                 headers={"Authorization": "Bearer " + token})
    # GET 重试（2026-11-23 实撞）：这个命名空间常态 120+ pod，`/api/v1/pods`
    # 的响应约 1MB；apiserver 偶发在 chunked 传输中途断流，`r.read()` 抛
    # `IncompleteRead` / `invalid literal for int() with base 16: b''`
    # —— 而原来只发一次，于是整轮 GC 判红（`BackoffLimitExceeded`），
    # 52 分钟前同一作业还是 Complete。清残骸这种活失败一轮不致命，
    # 但会让「磁盘/配额在涨」这件事失去唯一的自动清道夫。
    # 只重试 GET：DELETE 非幂等，重发可能删两次（虽然本脚本删的是终态 pod，
    # 语义上幂等，但别在这里赌）。
    attempts = 3 if method == "GET" else 1
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=30) as r:  # noqa: S310
                body = r.read().decode() or "{}"
                return json.loads(body)
        except Exception as exc:  # noqa: BLE001 — 逐次重试，最后一次才算数
            last = exc
            if attempt < attempts - 1:
                time.sleep(0.5 * (attempt + 1))
    raise last  # type: ignore[misc]


def run_namespace(namespace: str, args, now: datetime) -> dict:
    """清一个命名空间；返回该命名空间的一行结果（形状与旧版输出同）。"""
    if args.from_json:
        with open(args.from_json, encoding="utf-8") as fh:
            pods = (json.load(fh) or {}).get("items") or []
        token, ctx = "", None
        if args.jobs_from_json:
            with open(args.jobs_from_json, encoding="utf-8") as fh:
                failed_jobs = failed_job_names((json.load(fh) or {}).get("items") or [])
            jobs_map = "fixture"
        else:
            failed_jobs, jobs_map = None, "unavailable"
    else:
        token = open(f"{SA_DIR}/token", encoding="utf-8").read().strip()
        ctx = ssl.create_default_context(cafile=f"{SA_DIR}/ca.crt")
        pods = _api_call(f"/api/v1/namespaces/{namespace}/pods", token=token, ctx=ctx).get("items") or []
        try:
            jobs = _api_call(f"/apis/batch/v1/namespaces/{namespace}/jobs",
                             token=token, ctx=ctx).get("items") or []
            failed_jobs, jobs_map = failed_job_names(jobs), "ok"
        except Exception as exc:  # noqa: BLE001 - 取不到 Job 表不该让整轮不清理
            failed_jobs, jobs_map = None, unavailable_note(exc)

    dead, skipped, unparsed, held = select_pods(
        pods, now, args.min_age_hours, failed_jobs, args.forensic_hours, args.forensic_max)

    deleted, failed = 0, []
    if not args.dry_run:
        for name in dead:
            try:
                _api_call(f"/api/v1/namespaces/{namespace}/pods/{name}",
                          method="DELETE", token=token, ctx=ctx)
                deleted += 1
            except Exception as exc:  # noqa: BLE001 - 单条失败不该中断整批
                failed.append(f"{name}: {type(exc).__name__} {exc}")
    else:
        deleted = len(dead)

    return {
        "namespace": namespace,
        "min_age_hours": args.min_age_hours,
        "forensic_hours": args.forensic_hours,
        "forensic_max": args.forensic_max,
        "jobs_map": jobs_map,
        "candidates": len(dead),
        "deleted": deleted,
        "dry_run": bool(args.dry_run),
        "held_for_forensics": held,
        "skipped_finished_but_kept": skipped,
        "unparsed_timestamps": unparsed,
        "failed": failed[:5],
        "sample_deleted": dead[:5],
        "_checked_any": bool(pods or args.from_json),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="清理完成态 Pod（完成态仍占镜像层与配额）")
    # 可重复：`--namespace a --namespace b`。2026-11-22 之前只能清一个，
    # 于是 sub2api 攒了 1894 个 Evicted Pod 无人清（pod-gc 只扫 fd-prod），
    # 而那批残骸正是节点 DiskPressure 的放大器。
    ap.add_argument("--namespace", action="append", dest="namespaces",
                    metavar="NS",
                    default=None,
                    help="可重复；缺省取 POD_GC_NAMESPACE，再缺省 DEFAULT_NS")
    ap.add_argument("--min-age-hours", type=float,
                    default=float(os.environ.get("POD_GC_MIN_AGE_H") or DEFAULT_MIN_AGE_H))
    ap.add_argument("--forensic-hours", type=float,
                    default=float(os.environ.get("POD_GC_FORENSIC_H") or DEFAULT_FORENSIC_H),
                    help="失败 Job 的 Pod 封存多久（缺省 72h）；设 0 只用于自证")
    ap.add_argument("--dry-run", action="store_true", help="只列出，不删")
    ap.add_argument("--from-json", help="离线快照：{items:[pod…]}；缺省真打 API")
    ap.add_argument("--jobs-from-json", help="离线快照：{items:[job…]}；缺省真打 API")
    ap.add_argument("--forensic-max", type=int,
                    default=int(os.environ.get("POD_GC_FORENSIC_MAX") or DEFAULT_FORENSIC_MAX),
                    help="每个 CronJob 最多留几个失败 Job 的 Pod（缺省 2；设 0 只用于自证）")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)

    if args.namespaces:
        namespaces = [n for n in args.namespaces if n and n.strip()]
    else:
        env_ns = (os.environ.get("POD_GC_NAMESPACE") or "").strip()
        namespaces = [env_ns] if env_ns else [DEFAULT_NS]

    results = [run_namespace(ns, args, now) for ns in namespaces]
    for r in results:
        checked = r.pop("_checked_any")
        print(json.dumps(r, ensure_ascii=False))
        r["_checked_any"] = checked
    # 多命名空间时全 0 也必须是 0；只有「每个都一条没检查成」才 2。
    return 0 if any(r["_checked_any"] for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())
