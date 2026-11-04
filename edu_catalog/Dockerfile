# edu-catalog 镜像（add-education-content-catalog 7.3；五工具闭集：三只读+两订阅写面）
#
# 构建通道：.github/workflows/edu-catalog-image.yml → TCR（hkccr）→ cheap-3 tcr-relay
# 回灌 ccr（repos.conf 加 finddata/edu-catalog），部署侧 fd-infra-deploy/all-services/prod/
# edu-catalog.yaml（tag=commit sha，同 infra-telemetry 先例）。
# 只装查询面运行所需（fastmcp+uvicorn+psycopg），不装爬取依赖——镜像=最小权限形状。

ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE} AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY edu_catalog/ /app/edu_catalog/
COPY spiders/__init__.py /app/spiders/__init__.py
COPY spiders/education_catalog_core/ /app/spiders/education_catalog_core/
RUN pip install --no-cache-dir "fastmcp>=3" "uvicorn>=0.30" "psycopg[binary]>=3"

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    EDU_CATALOG_TRANSPORT=http \
    EDU_CATALOG_HOST=0.0.0.0 \
    EDU_CATALOG_PORT=8411

EXPOSE 8411
CMD ["python", "-m", "edu_catalog.server"]
