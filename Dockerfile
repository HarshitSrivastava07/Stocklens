# Railway builds from the repo root. This image packages apps/worker —
# the only deployable service (see README "Implementation status").
# Kept in sync with apps/worker/Dockerfile, which is used for local
# `docker build apps/worker`.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY apps/worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/worker/ .

RUN adduser --disabled-password --gecos "" workeruser
USER workeruser

CMD ["python", "upstox_ws.py"]
