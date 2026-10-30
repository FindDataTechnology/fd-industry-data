# infra-telemetry 镜像（add-infra-factory-fleet 组1，task 1.7）
#
# 构建通道：.github/workflows/infra-telemetry-image.yml → TCR（hkccr，公开仓免费线）。
# 只装 telemetry extra 运行所需（fastmcp+uvicorn），**不** pip 全仓——scrapling 等爬取
# 依赖与观测面无关，不进本镜像（镜像=最小权限形状，同 runner 镜像分线先例）。
# 只读工具面所需外部件：psql（④⑤通道）、kubectl（②通道，二进制构建期下载可重建）。

ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE} AS base

ARG APT_MIRROR=""
RUN if [ -n "$APT_MIRROR" ]; then \
        sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
        sed -i "s|deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list 2>/dev/null; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# kubectl 只读面：固定版本构建期下载（CN 构建机可 --build-arg 换镜像 URL）
ARG KUBECTL_VERSION=v1.31.4
ARG KUBECTL_BASE_URL=https://dl.k8s.io/release
RUN curl -fsSL -o /usr/local/bin/kubectl \
      "${KUBECTL_BASE_URL}/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod 0755 /usr/local/bin/kubectl \
    && kubectl version --client >/dev/null

WORKDIR /app
COPY infra_telemetry/ /app/infra_telemetry/
RUN pip install --no-cache-dir "fastmcp>=3" "uvicorn>=0.30"

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    FD_TELEMETRY_TRANSPORT=http \
    FD_TELEMETRY_HOST=0.0.0.0 \
    FD_TELEMETRY_PORT=8410 \
    FD_TELEMETRY_KUBE_CONTEXTS=in-cluster

EXPOSE 8410
# 无 ENTRYPOINT 包装：exec form 直点模块（k8s command 可覆；CRM 教训=镜像显式件齐全）
CMD ["python", "-m", "infra_telemetry.server"]
