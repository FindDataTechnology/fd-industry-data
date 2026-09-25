# fd-industry-runner: stable fat runtime. Shared lib (fd_industry_data) is baked
# in; content (spiders/) arrives via git sparse-checkout at pod start and
# shadows the baked snapshot. Adding a dependency here is a runtime release
# decision (see openspec gitops-crawl-runtime, design D3).
FROM docker.m.daocloud.io/library/python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY fd_industry_data/ ./fd_industry_data/
COPY spiders/ ./spiders/

RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
        . psycopg2-binary

ENTRYPOINT ["fd-runner"]
