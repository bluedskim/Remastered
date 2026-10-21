# syntax=docker/dockerfile:1
# linux/arm64 (Apple Silicon) 및 linux/amd64 모두 지원
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

FROM base AS runtime

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 1000 --shell /usr/sbin/nologin remastered \
    && mkdir -p /work /input /output /references /config \
    && chown remastered:remastered /work /input /output /references /config

USER remastered
WORKDIR /work

ENTRYPOINT ["remastered"]
CMD ["--help"]

FROM base AS dev

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests

RUN pip install --no-cache-dir ".[dev]"

WORKDIR /app
CMD ["pytest", "-q"]
