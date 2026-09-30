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

RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list 2>/dev/null; \
    apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
       xvfb x11vnc websockify novnc fonts-noto-cjk fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

FROM base AS gate
WORKDIR /w
COPY scripts/ scripts/
COPY spiders/ spiders/
COPY fd_industry_data/ ./fd_industry_data/
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple pyyaml \
    && python3 scripts/validate_manifests.py \
    && python3 scripts/conformance_gate.py --roots .

FROM base AS runner
WORKDIR /app
COPY pyproject.toml README.md ./
COPY fd_industry_data/ ./fd_industry_data/
COPY spiders/ ./spiders/
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
        . psycopg2-binary pyyaml cryptography minio playwright \
    && PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright \
       python3 -m playwright install --with-deps chromium
ENTRYPOINT ["fd-runner"]

FROM runner AS scan
COPY spiders/ /content/spiders/
COPY scripts/import_scan.py scripts/quarantine.txt /tmp/
WORKDIR /content
RUN FD_CONTENT_DIR=/content/spiders python3 /tmp/import_scan.py
