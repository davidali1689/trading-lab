# Pinned Lambda Web Adapter image (cicd-templates pattern)
ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.13.5-slim
ARG LAMBDA_ADAPTER_IMAGE=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.22

FROM ${PYTHON_IMAGE}

WORKDIR /app

COPY --from=${LAMBDA_ADAPTER_IMAGE} /lambda-adapter /opt/extensions/lambda-adapter
ENV PORT=8000
ENV AWS_LWA_INVOKE_MODE=buffered

COPY --from=${UV_IMAGE} /uv /usr/local/bin/uv

COPY pyproject.toml ./
COPY src/ ./src/
COPY api/ ./api/

RUN uv pip install --system --no-cache .
RUN uv pip install --system --no-cache "fastapi>=0.115" "uvicorn>=0.32" "boto3>=1.34" "tzdata>=2024.1"

ENV PYTHONPATH=/app/src
ENV PATH="/usr/local/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000"]
