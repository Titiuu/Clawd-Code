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

COPY ./ /home/app/

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install --no-build-isolation /home/app

RUN useradd --create-home --shell /bin/bash clawd \
    && mkdir -p /home/workspace \
    && chown -R clawd:clawd /workspace /home/clawd

USER clawd
WORKDIR /home/workspace

CMD ["python", "-m", "http.server", "31366"]
