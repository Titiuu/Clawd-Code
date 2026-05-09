FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY src ./src

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install --no-build-isolation .

RUN useradd --create-home --shell /bin/bash clawd \
    && mkdir -p /workspace \
    && chown -R clawd:clawd /workspace /home/clawd

USER clawd
WORKDIR /workspace

ENTRYPOINT ["clawd"]
