# 自愈闭环巡检器用的 GHA self-hosted runner（spider-self-heal-l2 task 1.4）
#
# 构建通道：.github/workflows/health-runner-image.yml → TCR（hkccr）。
# 目标机（xinru-server1）只需 docker pull + docker run，无需任何手工装包。
# runner 发行包在构建期下载（GitHub 托管 runner 出网快）；目标机不访问 GitHub releases。
FROM debian:bookworm-slim

ARG RUNNER_VERSION=2.337.0
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git jq libicu72 postgresql-client tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -d /home/runner -s /bin/bash runner

# actions runner 发行包（构建期下载，可重建、可升级：改 RUNNER_VERSION 即可）
RUN curl -fsSL -o /tmp/runner.tar.gz \
      "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
    && mkdir -p /actions-runner \
    && tar xzf /tmp/runner.tar.gz -C /actions-runner \
    && rm /tmp/runner.tar.gz \
    && /actions-runner/bin/installdependencies.sh \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY runner/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /data/runner \
    && chown -R runner:runner /actions-runner /data

USER runner
WORKDIR /actions-runner
ENTRYPOINT ["/entrypoint.sh"]