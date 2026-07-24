# syntax=docker/dockerfile:1

# ── 공통 ──────────────────────────────────────────────────────────────────
FROM node:24-slim AS base
ENV CI=true
WORKDIR /app

# ── 의존성 (package-lock 기준, 재현 가능 설치) ─────────────────────────────
FROM base AS deps
COPY package.json package-lock.json ./
RUN npm ci

# ── 개발 ──────────────────────────────────────────────────────────────────
# 소스는 compose가 바인드마운트한다. node_modules는 named volume이 덮어
# 호스트(Windows)의 플랫폼별 네이티브 모듈이 컨테이너를 오염시키지 않게 한다.
FROM deps AS dev
CMD ["npm", "run", "dev"]

# ── 빌드 → 정적 파일 ───────────────────────────────────────────────────────
FROM deps AS build
COPY . .
RUN npm run build

# ── 운영: nginx가 정적 파일 서빙 + /api 프록시 ─────────────────────────────
# nginx.conf는 이미지에 굽지 않는다 — 빌드 컨텍스트(./frontend) 밖의 infra/nginx에
# 있기 때문이다. docker-compose.prod.yml이 read-only로 마운트한다.
FROM nginx:1.27-alpine AS prod
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
