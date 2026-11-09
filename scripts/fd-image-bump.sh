#!/bin/bash
# fd-image-bump — runner 镜像 tag 的"采用"环节。
#
# WHY (2026-11-08)：`fd-industry-runner` 每次推 main 都会构建、cheap-3 的
# tcr-relay 也会同步到 ccr，但 Argo ApplicationSet `fd-industry-crawl` 的 helm
# 参数 `image` 是**手写死 tag**。于是管线全绿、镜像齐全，集群却一直跑几个月前
# 的那一片（实测 79 个 CronJob 停在 sha-750da3f）——集群里的 fd_industry_data
# 包比仓库旧，逼得爬虫写兜底导入、chart 扩 sparse-checkout。
#
# 与 `fd-image-pin-audit.sh` 互补，别混：
#   · pin-audit 问「我钉的 tag 还能拉到吗」（10-14 死 tag 事故）
#   · 本脚本问「我钉的 tag 是不是最新的」（11-08 静默落后事故）
# 两者都不覆盖对方。
#
# 用法（在能 kubectl 到 tencent 且有 gh 的机器上跑，如 cheap3 / 开发机）：
#   fd-image-bump.sh            # 只比对，打印 pinned vs newest（不改）
#   fd-image-bump.sh --check    # 同上，但漂移时 exit 1（可挂 cron 当哨兵）
#   fd-image-bump.sh --apply    # 把 appset 的 image 参数推到 newest，并等同步
##
# 退出码：0 一致或已更新；1 有漂移（--check 模式）；2 环境/参数问题。
set -uo pipefail

REPO="${REPO:-FindDataTechnology/fd-industry-data}"
WORKFLOW="${WORKFLOW:-image.yml}"
APPSET="${APPSET:-fd-industry-crawl}"
APPSET_NS="${APPSET_NS:-argocd}"
APP_NS="${APP_NS:-fd-industry}"
KUBECTL_CTX="${KUBECTL_CTX:-tencent}"
IMAGE_REPO="ccr.ccs.tencentyun.com/finddata/fd-industry-runner"
MODE="report"

for arg in "$@"; do
  case "$arg" in
    --check) MODE="check" ;;
    --apply) MODE="apply" ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "FATAL: unknown arg $arg" >&2; exit 2 ;;
  esac
done

# 锁文件：两次 apply 并发会把 appset 的 parameters 数组写乱。
# 锁：两次 apply 并发会把 appset 的 parameters 数组写乱。
# 但 macOS 没有 flock —— 缺了就**降级为不锁**并明确告知，而不是把脚本判死：
# 这个脚本主要在 cheap3（有 flock）跑，开发机只做 --check。
LOCK=/tmp/fd-image-bump.lock
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK" || { echo "FATAL: cannot open $LOCK" >&2; exit 2; }
  flock -n 9 || { echo "FATAL: another fd-image-bump is running" >&2; exit 2; }
else
  echo "WARN: no flock on this host — concurrency protection disabled (fine for --check)" >&2
fi

pinned=$(kubectl --context "$KUBECTL_CTX" -n "$APPSET_NS" get applicationset "$APPSET" \
  -o jsonpath='{.spec.template.spec.source.helm.parameters[?(@.name=="image")].value}' 2>/dev/null)
[ -n "$pinned" ] || { echo "FATAL: cannot read pinned image from appset $APPSET" >&2; exit 2; }

# 选片：**不是**"最新构建"，而是"ccr 上已可拉取的最新一片"。
# 踩过：每次推 main 都会出新片，构建永远快于 relay（实测连续两轮 1cb54b4、ae62d2e
# 都还没到 ccr），若拿最新构建当基准，脚本会永久报漂移却永远 apply 不了。
# 做法：从新到旧遍历最近的成功构建，取第一个 probe 通过的。
# 候选集：有 gh 就用构建记录；没有 gh（如 cheap3）就直接列 ccr 上的 tag。
# 两条路都只产出"sha-XXXXXXX"这一种形态，后面对齐。
# 注意：macOS 自带 bash 3.2 没有 mapfile，故用 while-read 填数组（可移植）。
#
# 2026-11-09 改动：workflow 现在有内容闸门（digest 变了才铸 sha- tag），所以
# 「每个成功构建都有一个 sha- tag」不再成立——只改文档/CI 的提交没有 tag。
# 因此候选上限从 25 提到 100：一次文档密集期可能连续几十个提交都不产片。
# probe 失败会自动跳到下一个候选，逻辑本身兼容，只是要多退几格。
CANDIDATES=()
while IFS= read -r line; do
  [ -n "$line" ] && CANDIDATES+=("$line")
done < <(
  if command -v gh >/dev/null 2>&1; then
    gh run list --repo "$REPO" --workflow "$WORKFLOW" --limit 100 \
      --json headSha,conclusion --jq '.[]|select(.conclusion=="success")|.headSha[0:7]' 2>/dev/null
  elif command -v skopeo >/dev/null 2>&1 && [ -r /etc/tcr-relay/env ]; then
    . /etc/tcr-relay/env
    skopeo list-tags --creds "${TCR_USER:-}:${TCR_PASS:-}" \
      "docker://$IMAGE_REPO" 2>/dev/null \
      | grep -oE '"sha-[0-9a-f]{7}"' | tr -d '"' | sed 's/^sha-//' | head -25
  fi
)
[ "${#CANDIDATES[@]:-0}" -gt 0 ] || { echo "FATAL: 无法获取候选 tag（既无 gh 也无 skopeo/凭据）" >&2; exit 2; }

probe_pullable() {
  local ref="$1"
  command -v skopeo >/dev/null 2>&1 || return 0   # 无 skopeo 时视为可拉（下面会告警）
  local creds=""
  if [ -r /etc/tcr-relay/env ]; then
    . /etc/tcr-relay/env
    creds="--creds ${TCR_USER:-}:${TCR_PASS:-}"
  fi
  # The runner image is LINUX/AMD64 ONLY. A bare `skopeo inspect` resolves the
  # image index against the LOCAL platform, so on an arm64 host (any Apple
  # Silicon dev box) every valid tag fails with "no image instance for
  # architecture arm64/darwin" and this probe reports it as "ccr 上还没有" —
  # silently skipping the genuinely newest slices and adopting an older one.
  # Live 2026-11-09: on an M-series Mac the script skipped sha-34b0330 (which
  # carries the jp-tdnet unit) and adopted sha-68ebd20, which predates it.
  # Pin the platform so "pullable" means "the nodes can pull it", not "this
  # laptop can run it".
  skopeo inspect --override-os linux --override-arch amd64 $creds "docker://$ref" >/dev/null 2>&1
}

has_skopeo=1; command -v skopeo >/dev/null 2>&1 || has_skopeo=0
[ "$has_skopeo" = "1" ] || echo "WARN: 无 skopeo，无法判断 tag 是否已到 ccr（可能推到拉不到的片）" >&2

newest=""
for sha in ${CANDIDATES[@]+"${CANDIDATES[@]}"}; do
  cand="$IMAGE_REPO:sha-$sha"
  if probe_pullable "$cand"; then newest="$cand"; break; fi
  echo "  skip sha-${sha}（ccr 上还没有，relay 未同步）"
done
[ -n "$newest" ] || { echo "FATAL: 最近 ${#CANDIDATES[@]:-0} 片构建在 ccr 上全部不可拉，查 relay" >&2; exit 2; }

echo "pinned = ${pinned}"
echo "adopt  = ${newest}   （ccr 上已可拉取的最新一片）"

if [ "$pinned" = "$newest" ]; then
  echo "RESULT: 一致（无需变动）"
  exit 0
fi

echo "RESULT: 漂移——集群仍在跑 $(basename "$pinned")，最新是 ${newest##*:}"
[ "$MODE" = "check" ] && exit 1

if [ "$MODE" != "apply" ]; then
  echo "（只报告；加 --apply 才会 patch appset）"
  exit 0
fi

# apply：备份 → patch → 等 Application 同步 → 核对 CronJob
BAK="/tmp/appset-$APPSET-$(date +%Y%m%d-%H%M%S).yaml"
kubectl --context "$KUBECTL_CTX" -n "$APPSET_NS" get applicationset "$APPSET" -o yaml > "$BAK" \
  || { echo "FATAL: backup failed" >&2; exit 2; }
echo "备份：$BAK"

idx=$(kubectl --context "$KUBECTL_CTX" -n "$APPSET_NS" get applicationset "$APPSET" \
  -o jsonpath='{range .spec.template.spec.source.helm.parameters[*]}{.name}{"\n"}{end}' \
  | grep -n '^image$' | cut -d: -f1)
[ -n "$idx" ] || { echo "FATAL: image parameter not found in appset" >&2; exit 2; }
path="/spec/template/spec/source/helm/parameters/$((idx-1))/value"

kubectl --context "$KUBECTL_CTX" -n "$APPSET_NS" patch applicationset "$APPSET" \
  --type=json -p "[{\"op\":\"replace\",\"path\":\"$path\",\"value\":\"$newest\"}]" \
  || { echo "FATAL: patch failed" >&2; exit 2; }

# 等 Application 同步（appset 生成 → Argo sync → CronJob 重渲染），最多 ~3 分钟
app="${APPSET/fd-industry-crawl/fd-industry-nfra-penalty}"
for i in $(seq 1 12); do
  sleep 15
  s=$(kubectl --context "$KUBECTL_CTX" -n "$APPSET_NS" get application "$app" \
      -o jsonpath='{.status.sync.status}' 2>/dev/null)
  if [ "$s" = "Synced" ]; then
    cur=$(kubectl --context "$KUBECTL_CTX" -n "$APP_NS" get cronjob "$app" \
          -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].image}' 2>/dev/null)
    if [ "$cur" = "$newest" ]; then
      echo "RESULT: 已更新，$app 现在跑 ${newest##*:}"
      echo "注意：其余 appset 源的 CronJob 会随后陆续重渲染；用下面这条自查："
      echo "  kubectl --context $KUBECTL_CTX -n $APP_NS get cronjobs -o jsonpath='{range .items[*]}{.spec.jobTemplate.spec.template.spec.containers[0].image}{\"\\n\"}{end}' | sed 's/.*://' | sort | uniq -c"
      exit 0
    fi
  fi
done
echo "WARN: patch 已下发，但 $app 尚未在 3 分钟内切到新 tag，请人工核对" >&2
exit 1
