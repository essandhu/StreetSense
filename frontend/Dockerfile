# StreetSense frontend image — Phase 5 Task 1.3, fallback path.
#
# Two stages: pnpm build → nginx serve. Provided for the
# single-VPS + docker compose fallback shape (ADR 0008's documented
# fallback). The primary Fly.io deploy uses api/Dockerfile, which
# bakes the built SPA into the API image directly via FastAPI's
# StaticFiles mount — that's one image, one process, simpler.
#
# This image is useful when:
#   - Deploying the frontend behind a CDN (Cloudflare, etc.) where
#     the static assets live separately from the API process.
#   - Running docker-compose.prod.yml on a single VPS where the API
#     image and a CDN/reverse-proxy fronting nginx make sense as
#     separate containers.
#
# Run:
#   docker build -f frontend/Dockerfile -t streetsense-frontend .
#   docker run -p 80:80 streetsense-frontend

FROM node:22-bookworm-slim AS build

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    CI=1

RUN corepack enable && corepack prepare pnpm@9.15.0 --activate

WORKDIR /app

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./

# `VITE_API_BASE_URL` is the one env var the bundle bakes in at build
# time. Default is the same-origin (empty string ⇒ /api/* relative
# paths via reverse proxy). Override via `--build-arg`.
ARG VITE_API_BASE_URL=""
ARG VITE_TILE_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_TILE_BASE_URL=${VITE_TILE_BASE_URL}

RUN pnpm build

# ---- runtime ------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# SPA routing: every unknown path serves index.html so React Router
# (none today; future-proofed) and direct deep-links to /delta /
# methodology don't 404. Plus aggressive cache headers on hashed
# Vite asset filenames.
COPY <<'EOF' /etc/nginx/conf.d/default.conf
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    # Hashed Vite assets — long cache, immutable.
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
        try_files $uri =404;
    }

    # SPA fallback — any unknown path renders the SPA entry point.
    location / {
        try_files $uri /index.html;
    }
}
EOF

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# nginx:alpine ships an entrypoint that forwards SIGTERM correctly;
# the default CMD is `nginx -g 'daemon off;'`.
