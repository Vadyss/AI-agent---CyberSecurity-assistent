# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
# Base image pinned by digest; update deliberately (see Docs/OPERATIONS.md).
ARG PYTHON_IMAGE=python:3.12.15-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.lock /tmp/requirements.lock
# Every package, including transitive ones, must match a pinned SHA-256 hash.
RUN python -m venv /venv \
    && /venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock \
    && /venv/bin/pip uninstall -y pip

FROM ${PYTHON_IMAGE}
ARG APP_VERSION=dev
LABEL org.opencontainers.image.authors="Adam Krúpa, Ondra Csajka" \
      org.opencontainers.image.title="Toolsmith backend" \
      org.opencontainers.image.version="${APP_VERSION}"
ENV PATH=/venv/bin:$PATH PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_VERSION=${APP_VERSION}
# GID 10001 is fixed: the sandbox grants its socket directory to this group.
RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /data /run/toolsmith && chown appuser:appuser /data
COPY --from=build /venv /venv
WORKDIR /app
# Code stays owned by root, so the service user cannot modify it.
COPY alembic.ini ./
COPY storage/ ./storage/
COPY identity/ ./identity/
COPY orchestrator/ ./orchestrator/
COPY gatekeeper/ ./gatekeeper/
COPY examiner/ ./examiner/
COPY policy/ ./policy/
COPY seed_skills/ ./seed_skills/
COPY datasets/ ./datasets/
COPY scripts/verify_audit.py scripts/llm_smoke.py scripts/live_test.py ./scripts/
USER 10001
EXPOSE 8000 9100
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

# --factory: configuration is read and validated when the server starts, not at import.
CMD ["uvicorn", "orchestrator.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--ws", "websockets", "--ws-ping-interval", "20", "--ws-ping-timeout", "20"]
