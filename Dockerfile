FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/home/app \
    CLAWD_SKILLS_DIR=/root/.clawd/skills \
    CLAWD_RUNTIME_ROOT=/root/.clawd/runtime \
    CLAWD_WORKSPACE_ROOT=/workspace/sessions

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

RUN mkdir -p /root/.clawd/skills /root/.clawd/runtime /workspace/sessions

WORKDIR /workspace

EXPOSE 31366

CMD ["uvicorn", "src.server.app:app", "--host", "0.0.0.0", "--port", "31366"]
