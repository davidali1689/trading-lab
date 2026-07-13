# Pinned Lambda Web Adapter image (cicd-templates pattern)
ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.13.5-slim
ARG LAMBDA_ADAPTER_IMAGE=public.ecr.aws/awsguru/aws-lambda-adapter:1.0.0
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.22

FROM ${LAMBDA_ADAPTER_IMAGE} AS lambda-adapter
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE}

WORKDIR /app

# Lambda Web Adapter — inert for local `docker run`; active on Lambda.
COPY --from=lambda-adapter /lambda-adapter /opt/extensions/lambda-adapter
ENV PORT=8000
ENV AWS_LWA_INVOKE_MODE=buffered

COPY --from=uv /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
COPY src/ ./src/
COPY api/ ./api/

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000"]
