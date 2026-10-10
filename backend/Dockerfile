# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
FROM python:3.12-slim
LABEL org.opencontainers.image.authors="Adam Krúpa, Ondra Csajka"

WORKDIR /app
ENV PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
# GID 10001 is fixed: the sandbox grants its socket directory to this group.
RUN groupadd --gid 10001 appuser && useradd --create-home --uid 10001 --gid 10001 appuser \
    && mkdir -p /data /run/toolsmith && chown appuser:appuser /data
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

# --factory: configuration is read and validated when the server starts, not at import.
CMD ["uvicorn", "orchestrator.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--ws", "websockets", "--ws-ping-interval", "20", "--ws-ping-timeout", "20"]
