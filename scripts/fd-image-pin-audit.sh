#!/bin/bash
# fd-image-pin-audit — pinned-image existence audit (2026-10-14 incident).
#
# WHY: on 2026-10-14 a node image-GC event forced re-pulls and the entire legal
# line went into ImagePullBackOff. Root cause was NOT the pull: 11 live CronJobs
# pinned ccr tag sha-7d89e71, which had been evicted from ccr while still
# sitting on hkccr, and the relay's seen-ledger by design never re-copies an
# already-seen tag. Three more objects pinned the retired Harbor outright.
# Nothing in the fleet watched "is the tag I pin actually pullable".
#
# WHAT: for every CronJob/Deployment/StatefulSet/DaemonSet/Job on every cluster,
# take its container images (ccr.ccs.tencentyun.com and 100.64.0.8:30880 only)
# and `skopeo inspect` them with the TCR creds. Anything unresolvable is a
# latent ImagePullBackOff the moment this node evicts its cached copy.
# Suspended CronJobs are reported too (they become bombs the day someone
# flips suspend=false).
#
# Output: one MISSING_PIN line per missing pin + a final AUDIT_DONE summary.
# Exit 1 when anything is missing, 0 when clean, 2 on setup failure.
#
# Deploy on cheap3 (has kubectl + skopeo + /etc/tcr-relay/env):
#   scp scripts/fd-image-pin-audit.sh cheap3:/usr/local/bin/
#   printf '17 7 * * * root /usr/local/bin/fd-image-pin-audit.sh >> /var/log/fd-image-pin-audit.log 2>&1\n' \
#     > /etc/cron.d/fd-image-pin-audit
set -u
CREDF=/etc/tcr-relay/env
[ -r "$CREDF" ] && . "$CREDF" || { echo "FATAL: $CREDF unreadable" >&2; exit 2; }

CTXS="${FD_AUDIT_CONTEXTS:-cheap tencent xinru xinru-server1 xinru-server2 zihan}"
TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT

for ctx in $CTXS; do
  out=$(kubectl --context "$ctx" get cronjobs,deployments,statefulsets,daemonsets,jobs -A -o json 2>/dev/null) || continue
  [ -z "$out" ] && continue
  echo "$out" | python3 -c '
import json,sys
d=json.load(sys.stdin)
for it in d.get("items",[]):
    ns=it["metadata"]["namespace"]; name=it["metadata"]["name"]; kind=it["kind"]
    if kind=="CronJob":
        sus=it["spec"].get("suspend",False); pod=it["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    elif kind=="Job":
        sus=""; pod=it["spec"]["template"]["spec"]
    else:
        sus=""; pod=it.get("spec",{}).get("template",{}).get("spec",{})
    for c in pod.get("containers",[]):
        im=c["image"]
        if im.startswith("ccr.ccs.tencentyun.com/") or im.startswith("100.64.0.8:30880/"):
            print(f"{ns}|{kind}|{name}|{sus}|{im}")
' | sort -u | sed "s|^|$ctx|" >> "$TMP"
done

MISSING=0; CHECKED=0
while IFS='|' read -r ctx ns kind name sus im; do
  CHECKED=$((CHECKED+1))
  if ! timeout 45 skopeo inspect --no-tags --creds "$TCR_USER:$TCR_PASS" "docker://$im" >/dev/null 2>&1; then
    MISSING=$((MISSING+1))
    echo "MISSING_PIN ctx=$ctx ns=$ns kind=$kind name=$name suspend=$sus image=$im"
  fi
done < "$TMP"

# 空跑必须响。2026-11-09 实测：在 cheap3 上跑出 `missing=0 checked=0`——那台机的
# 唯一 context 指向 127.0.0.1:6443（拒绝连接），一个负载都没扫到。这是最坏的一种
# 绿灯：审计什么都没验，却和"全部可拉"长得一模一样（同类教训见「验证必须对准坏的
# 那个本身」）。
if [ "$CHECKED" -eq 0 ]; then
  echo "AUDIT_EMPTY checked=0 —— 没扫到任何 ccr/100.64 镜像，本次审计不成立" >&2
  echo "  查：kubectl --context <ctx> get cronjobs -A 是否可读；FD_AUDIT_CONTEXTS 是否配错；本机 kubeconfig 是否缺失" >&2
  exit 2
fi

echo "AUDIT_DONE missing=$MISSING checked=$CHECKED"
[ "$MISSING" -gt 0 ] && exit 1
exit 0
