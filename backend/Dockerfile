# syntax=docker/dockerfile:1

# ============================================================================
# 백엔드 이미지 — dev / prod 두 타깃
#
# venv를 /opt/venv 에 두는 이유: dev에서 ./backend 를 /app 에 바인드마운트하는데,
# 의존성이 /app 안에 설치돼 있으면 마운트가 그것을 통째로 가려서
# "컨테이너는 떴는데 import 에러"가 난다.
# ============================================================================

FROM python:3.13-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    TZ=UTC
RUN python -m venv /opt/venv
WORKDIR /app

# ── 런타임 의존성 (레이어 캐시: requirements.txt가 안 바뀌면 재설치 없음) ──
FROM base AS deps
COPY requirements.txt ./
RUN pip install -r requirements.txt

# ── 개발 ──────────────────────────────────────────────────────────────────
# 소스는 compose가 바인드마운트한다. 이미지에 COPY하지 않는다.
FROM deps AS dev
COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ── 운영 ──────────────────────────────────────────────────────────────────
# 소스를 이미지에 굽고 비root로 실행한다.
FROM deps AS prod
COPY . /app
RUN useradd --system --uid 10001 --no-create-home appuser \
 && chown -R appuser:appuser /app
USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
