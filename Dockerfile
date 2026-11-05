# EdgeForge: API and trader app in one image (derived artefacts only; no raw data, no player names).
# Build the serving bundle first:  uv run edgeforge serve build
# Build:  docker build -t edgeforge .      Run API:  docker run -p 8080:8080 edgeforge
# Run app: docker run -e SERVICE=app -p 8080:8080 edgeforge
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PIP_NO_CACHE_DIR=1
RUN pip install "uv==0.12.5"
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev
COPY configs ./configs
COPY apps ./apps
COPY artifacts/metrics ./artifacts/metrics
COPY artifacts/figures ./artifacts/figures
COPY artifacts/shadow ./artifacts/shadow
COPY data/serving ./data/serving
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" EDGEFORGE_BUNDLE=/app/data/serving PORT=8080 SERVICE=api
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/%s' % (os.environ.get('PORT','8080'), 'healthz' if os.environ.get('SERVICE','api')=='api' else '_stcore/health'))" || exit 1
CMD ["/entrypoint.sh"]
