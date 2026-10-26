# syntax=docker/dockerfile:1.7

FROM node:20-alpine AS deps

WORKDIR /app

ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_FUND=false

RUN apk add --no-cache bash

COPY package.json package-lock.json ./

RUN --mount=type=cache,target=/root/.npm \
    npm ci --ignore-scripts --no-audit --no-fund


FROM deps AS source

COPY . .


FROM source AS test

ENV NODE_ENV=test

# PR 只改了源码时传入改动文件，只跑受影响的测试；空值跑全量（master 与改配置/依赖的 PR）。
ARG UI_TEST_RELATED=""

RUN npm run lint

RUN if [ -n "$UI_TEST_RELATED" ]; then \
      npx vitest related --run --passWithNoTests $UI_TEST_RELATED; \
    else \
      npm test; \
    fi


FROM source AS builder

# NEXT_PUBLIC_* 会在构建时写入浏览器 bundle。
ARG NEXT_PUBLIC_AUTH_SERVICE_BASE_URL
ARG NEXT_PUBLIC_AUTH_SERVICE_CLIENT_ID
ARG NEXT_PUBLIC_AUTH_CALLBACK_URL
# 产品埋点（PostHog）：项目 key 是公开 token；采集地址同时决定 /ingest 反代目标。都不填则埋点关闭。
ARG NEXT_PUBLIC_POSTHOG_KEY
ARG NEXT_PUBLIC_POSTHOG_HOST

# Next.js rewrites() 在构建时读取 API_BACKEND_URL。
ARG API_BACKEND_URL=http://fusion-api:8000

ENV NEXT_PUBLIC_AUTH_SERVICE_BASE_URL=${NEXT_PUBLIC_AUTH_SERVICE_BASE_URL} \
    NEXT_PUBLIC_AUTH_SERVICE_CLIENT_ID=${NEXT_PUBLIC_AUTH_SERVICE_CLIENT_ID} \
    NEXT_PUBLIC_AUTH_CALLBACK_URL=${NEXT_PUBLIC_AUTH_CALLBACK_URL} \
    NEXT_PUBLIC_POSTHOG_KEY=${NEXT_PUBLIC_POSTHOG_KEY} \
    NEXT_PUBLIC_POSTHOG_HOST=${NEXT_PUBLIC_POSTHOG_HOST} \
    API_BACKEND_URL=${API_BACKEND_URL}

# .next/cache 走 BuildKit 缓存挂载：同一 builder 的下次构建可复用编译缓存，且不进入镜像层。
RUN --mount=type=cache,target=/app/.next/cache npm run build


FROM node:20-alpine AS production-deps

WORKDIR /app

ENV NODE_ENV=production \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_FUND=false

COPY package.json package-lock.json ./

RUN --mount=type=cache,target=/root/.npm \
    npm ci --ignore-scripts --omit=dev --no-audit --no-fund


FROM node:20-alpine AS production

WORKDIR /app

ENV NODE_ENV=production

COPY --from=production-deps /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/next.config.js ./next.config.js
COPY --from=builder /app/src ./src

EXPOSE 3000

CMD ["npm", "run", "start"]
