# fd-industry-runner multi-stage build.
#
# Targets:
#   gate  — content admission checks (manifest v2 + conformance) over the
#           checked-out tree; build fails red on violation. No runtime use.
#   runner — the stable fat runtime (default). Shared lib (fd_industry_data)
#           is baked in; content (spiders/) arrives via git sparse-checkout
#           at pod start and shadows the baked snapshot. Adding a dependency
#           here is a runtime release decision (openspec gitops-crawl-runtime,
#           design D3).
#   scan  — FROM runner + the checkout's spiders; imports every spider module
#           against the image's dependency surface. Build-only.
#
# NOTE: Jenkins agents are k8s pods, so bind-mounting the workspace into
# docker run does not work (host daemon cannot see pod paths). All checks
# must ride the docker build context.

# Base image and apt mirror are ARG-able: empty defaults = upstream sources
# (GitHub Actions, US runners). CN build boxes pass
#   --build-arg BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim --build-arg APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE} AS base
ARG APT_MIRROR=""
ARG PIP_INDEX_URL=""

RUN if [ -n "$APT_MIRROR" ]; then \
        sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
        sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list 2>/dev/null; \
    fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
       fonts-noto-cjk fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
# 浏览器显示栈（xvfb/x11vnc/websockify/novnc）已移除：164 个 spider 里**零个**
# 真的用浏览器（2026-11-22 逐个核过，见 scripts/check_dead_session_declarations.py）。
# fonts 两个留着——PDF/图表文本渲染仍要用。

FROM base AS gate
WORKDIR /w
COPY scripts/ scripts/
COPY spiders/ spiders/
COPY fd_industry_data/ ./fd_industry_data/
COPY universes/ universes/
# 内容准入闸：清单健全 + 一致性。**只有 runner 阶段 COPY 了 /w/.content-gate-ok
# 这一行，这个阶段才会真的被构建**——BuildKit 只构建「从 target 可达」的阶段，
# 而在 2026-11-15 之前没有任何阶段依赖 gate、image.yml 也没设 target ⇒ 本阶段
# 是孤儿：注释写着「每次构建都跑闸」，实际一次都没跑过（历史构建日志里
# `[gate` 出现 0 次）。加标记文件是为了让「闸跑过」成为构建图的硬依赖，
# 而不是靠注释承诺。
RUN PIP_FLAGS=""; [ -n "$PIP_INDEX_URL" ] && PIP_FLAGS="-i $PIP_INDEX_URL"; \
    pip install --no-cache-dir $PIP_FLAGS pyyaml \
    && python3 scripts/validate_manifests.py \
    && python3 scripts/conformance_gate.py --roots . \
    && date -u +%FT%TZ > /w/.content-gate-ok

FROM base AS runner
WORKDIR /app
# 这一行是**功能性的依赖**，不是取文件：它让 `gate` 阶段进入构建图。删掉它，
# 构建仍会成功，而内容准入闸（validate_manifests + conformance_gate）会静默
# 退出流水线——2026-11-15 之前正是这个状态（gate 是孤儿阶段，历史日志里
# `[gate` 0 次）。标记内容在 /w/.content-gate-ok（UTC 时刻），体积 21 字节。
COPY --from=gate /w/.content-gate-ok /app/.content-gate-ok
COPY pyproject.toml README.md ./
COPY fd_industry_data/ ./fd_industry_data/
COPY spiders/ ./spiders/
# universes/ — 共享金融车道的默认宇宙注册表（vertical-lane-generalization）。
# 车道单元运行期从 universes/<region>.yaml 读默认宇宙，缺此目录即硬失败。
COPY universes/ ./universes/
# poppler-utils — pdftotext for the PDF-only upstreams (add-banking-industry-sources:
# NFRA 批量公示表 / 上清所月报表四表五 / CBA 理财指数 2026 PDF / 银登不良转让公告).
# Consumed through fd_industry_data.pdftext (single subprocess wrapper), never
# hand-rolled per unit. Runtime release decision per gitops-crawl-runtime D3.
# libatomic1 已随 playwright 一并移除（它只为 playwright 的 node 存在）。
# 历史：2026-11-11 main 上连续三次 image 构建因缺它失败——留此注记，恢复本地
# 渲染时要连它一起加回来。
RUN if [ -n "$APT_MIRROR" ]; then \
        sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
        sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list 2>/dev/null; \
    fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*
# playwright + chromium 不再装进本镜像：Chromium 在镜像里占 700MB，而为零个实际
# spider 服务（2026-11-22 实测）。需要渲染的腿走 BROWSER_CDP_URL 指向独立浏览器
# 服务——fd_industry_data/browser.py 早就支持这条路（一个 env 就切远程）。
#
# 若要恢复本地渲染：把 playwright 加回下面这行，并补回上面那段
# `playwright install --with-deps chromium` 与 libatomic1。
RUN PIP_FLAGS=""; [ -n "$PIP_INDEX_URL" ] && PIP_FLAGS="-i $PIP_INDEX_URL"; \
    pip install --no-cache-dir $PIP_FLAGS \
        . psycopg2-binary pyyaml cryptography minio xlrd
# `pip install .` 产出的 site-packages 副本在 sys.path 更前，会盖过上面
# COPY 进来的 /app 那份 —— 而它缺 package-data 里的 json。实测（2026-11-20）：
#   /app/fd_industry_data/alerts_core/          有 caas-forms.json
#   /usr/local/lib/.../alerts_core/             没有
# 于是 resolve_target() 抛 FileNotFoundError，七条告警线真 firing 时全崩。
# 显式补齐，不依赖 setuptools 的 package-data 通配（改它不一定改变镜像内容）。
RUN cp -f /app/fd_industry_data/alerts_core/*.json \
       /usr/local/lib/python3.12/site-packages/fd_industry_data/alerts_core/

ENTRYPOINT ["fd-runner"]

FROM runner AS scan
COPY spiders/ /content/spiders/
COPY scripts/import_scan.py scripts/quarantine.txt /tmp/
WORKDIR /content
RUN FD_CONTENT_DIR=/content/spiders python3 /tmp/import_scan.py
