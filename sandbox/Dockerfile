# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
ARG PYTHON_IMAGE=python:3.12.15-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.lock /tmp/requirements.lock
RUN python -m venv /venv \
    && /venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock \
    && /venv/bin/pip uninstall -y pip

FROM ${PYTHON_IMAGE}
LABEL org.opencontainers.image.authors="Adam Krúpa, Ondra Csajka" \
      org.opencontainers.image.title="Toolsmith sandbox"
ENV PATH=/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SANDBOX_ISOLATION=uid SANDBOX_UID_BASE=20000
COPY --from=build /venv /venv
WORKDIR /app
COPY main.py server.py runner.py ./
# The supervisor starts as UID 0 so it can hand every job a private,
# unprivileged UID. Compose drops all capabilities except SETUID, SETGID and
# KILL, sets no-new-privileges and a read-only root file system. Submitted
# code never runs as UID 0 and holds no capabilities. Jobs start the runner with
# "python -I -S", so no site-packages (venv or system) are importable: only the
# standard library modules on the allow-list.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 CMD ["python", "server.py", "--health"]

CMD ["python", "server.py"]
