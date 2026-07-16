# CurricMesh frontend — production image.
# Multi-stage: build the Vite SPA, then serve the static bundle with nginx.

# --- Stage 1: build ---------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app

# VITE_API_URL is baked into the bundle at build time. Same-origin default
# (`/api/v1`) means the browser calls https://<domain>/api/v1/... and Caddy
# reverse-proxies /api/* to the backend — no CORS dance.
ARG VITE_API_URL=/api/v1
ENV VITE_API_URL=${VITE_API_URL}

# Install deps first (cached layer) using the lockfile for reproducible builds.
COPY package.json package-lock.json ./
RUN npm ci

# Build the SPA.
COPY . .
RUN npm run build

# --- Stage 2: serve ---------------------------------------------------------
FROM nginx:alpine AS serve

# SPA-fallback nginx config (try_files ... /index.html).
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Static bundle from the build stage.
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
