# marketing-signals 镜像（arm-marketing-signal-scout 实现段的出口）
#
# 两工具闭集（signals_recent / signals_by_entity，全只读）的 MCP 面。
#
# 构建通道：.github/workflows/marketing-signals-image.yml → TCR（hkccr）→ cheap9
# tcr-relay 回灌 ccr（repos.conf 加 finddata/marketing-signals），部署侧
# fd-infra-deploy/all-services/prod/marketing-signals.yaml（tag=commit sha，
# 同 edu-catalog 先例）。
#
# 只装查询面运行所需（fastmcp + lxml）：信号归一读的是既有 spider 的**函数**，
# 其中 NFRA 走 HTML 表格解析要 lxml；不装 psycopg / 爬取重依赖——本面不写库。
#
# 边界：无任何触达类工具（自检断言），本镜像不带 SMTP / webhook 依赖。

ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 只要归集模块 + 三个已验证源的 spider（不整仓 COPY：镜像=最小权限形状）
COPY fd_industry_data/__init__.py /app/fd_industry_data/__init__.py
COPY fd_industry_data/marketing_signal.py /app/fd_industry_data/marketing_signal.py
COPY fd_industry_data/marketing_signal_server.py /app/fd_industry_data/marketing_signal_server.py
COPY fd_industry_data/tabular/ /app/fd_industry_data/tabular/
COPY fd_industry_data/http.py /app/fd_industry_data/http.py
COPY spiders/__init__.py /app/spiders/__init__.py
COPY spiders/ted-notices/ /app/spiders/ted-notices/
COPY spiders/worldbank-procnotices/ /app/spiders/worldbank-procnotices/
COPY spiders/nfra-penalty/ /app/spiders/nfra-penalty/

RUN pip install --no-cache-dir "fastmcp>=3" "lxml>=5"

ENV PYTHONPATH=/app \
    MARKETING_SIGNALS_TRANSPORT=http \
    MARKETING_SIGNALS_HOST=0.0.0.0 \
    MARKETING_SIGNALS_PORT=8412

EXPOSE 8412
CMD ["python", "-m", "fd_industry_data.marketing_signal_server"]