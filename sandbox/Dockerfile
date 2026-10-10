# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
FROM python:3.12-slim
LABEL org.opencontainers.image.authors="Adam Krúpa, Ondra Csajka"

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SANDBOX_ISOLATION=uid SANDBOX_UID_BASE=20000

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py server.py runner.py ./
# The supervisor starts as UID 0 so it can hand every job a private,
# unprivileged UID. Compose drops all capabilities except SETUID, SETGID and
# KILL, sets no-new-privileges and a read-only root file system. Submitted
# code never runs as UID 0 and holds no capabilities.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["python", "server.py"]
