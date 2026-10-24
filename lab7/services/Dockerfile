FROM ghcr.io/github/github-mcp-server:2.0.3 AS github-mcp
FROM alpine/helm:4.1.0 AS helm

FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv wget \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /venv \
    && /venv/bin/pip install --no-cache-dir \
        PyYAML==6.0.3 \
    && npm install --global kubernetes-mcp-server@0.0.69

COPY --from=github-mcp /server/github-mcp-server /usr/local/bin/github-mcp-server
COPY --from=helm /usr/bin/helm /usr/local/bin/helm

WORKDIR /app
COPY services/*.py /app/

EXPOSE 8000
CMD ["/venv/bin/python", "/app/agent.py"]
