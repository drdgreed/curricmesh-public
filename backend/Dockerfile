# CurricMesh backend — production image.
# Runtime deps only (`pip install -e "."`, NOT ".[dev]" which pulls test deps).
FROM python:3.11-slim

# - PYTHONUNBUFFERED: stream logs immediately (no buffering) for platform log capture.
# - PYTHONDONTWRITEBYTECODE: skip .pyc clutter in the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ---------------------------------------------------------------------------
# Slide render toolchain (S1 — the one new deploy dependency)
# ---------------------------------------------------------------------------
# app/slides/render.py shells out to marp-cli (deck.md -> PDF/PPTX/HTML) and
# mermaid-cli (.mmd -> PNG). Both drive a HEADLESS CHROMIUM via puppeteer, so
# the image needs Node.js + the two CLIs (PINNED to CareerFoundry versions.env
# for reproducible output) + a Chromium binary. render.py invokes the CLIs with
# `npx --yes <pkg>@<version>`; installing the SAME versions globally means npx
# resolves them locally (no per-render network download).
#
# Sandbox note: Chromium's setuid sandbox does not work for the non-root
# appuser in a container. Prod must run the render CLIs with a no-sandbox
# puppeteer config (baked at /etc/puppeteer/config.json below) — see
# docs/SLIDE_SYSTEM.md "Deploying the render toolchain". CI never runs the real
# render (it mocks the subprocess), and the local smoke script runs on a host
# with a working sandbox, so this caveat is prod-deploy only.
ENV MARP_VERSION=4.4.0 \
    MERMAID_VERSION=11.16.0 \
    PUPPETEER_SKIP_DOWNLOAD=1 \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    CHROME_PATH=/usr/bin/chromium \
    PUPPETEER_CONFIG=/etc/puppeteer/config.json

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg chromium fonts-liberation; \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -; \
    apt-get install -y --no-install-recommends nodejs; \
    npm install -g "@marp-team/marp-cli@${MARP_VERSION}" "@mermaid-js/mermaid-cli@${MERMAID_VERSION}"; \
    mkdir -p /etc/puppeteer; \
    printf '{"args":["--no-sandbox","--disable-gpu","--disable-dev-shm-usage"]}' > /etc/puppeteer/config.json; \
    apt-get purge -y --auto-remove curl gnupg; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the backend source. `.dockerignore` keeps venv/, caches, and .env out.
# pyproject.toml's setuptools find includes both `app*` and `seed*`, and the
# build needs the package source present, so copy first then install.
COPY . .

# Editable runtime install. -e "." installs only the runtime dependency set.
RUN pip install --no-cache-dir -e "." \
    && chmod +x ./docker-entrypoint.sh

# Run as a non-root user (defence in depth — the container needs no root).
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Entrypoint runs `alembic upgrade head` then `exec uvicorn ...`.
# PORT is honoured if the platform injects one (Render/Railway), else 8000.
ENTRYPOINT ["./docker-entrypoint.sh"]
