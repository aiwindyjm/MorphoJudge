FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS base
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
RUN npm install --global pnpm@10.30.3

FROM base AS dependencies
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

FROM dependencies AS development
COPY next.config.ts next-env.d.ts tsconfig.json ./
COPY app ./app
COPY public ./public
RUN mkdir -p public && chown -R node:node /app
USER node
EXPOSE 3000
CMD ["pnpm", "dev", "--hostname", "0.0.0.0", "--webpack"]

FROM dependencies AS builder
COPY next.config.ts next-env.d.ts tsconfig.json ./
COPY app ./app
COPY public ./public
RUN mkdir -p public && pnpm build

FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS runner
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
WORKDIR /app
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]

# ---------------------------------------------------------------------------
# daemon: MorphoJudge 本地分析服务（OPS-000）
# 只读分析边界：不挂载 PRIVATE/Docker socket/用户目录；宿主不发布端口；
# 非 root 用户运行；pytest 在容器内通过 compose exec 执行。
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS daemon
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/engine \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /engine
COPY engine/pyproject.toml ./pyproject.toml
RUN python -c "import subprocess, sys, tomllib; \
    data = tomllib.load(open('pyproject.toml', 'rb')); \
    deps = list(data['project']['dependencies']) + list(data['project']['optional-dependencies']['test']); \
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', *deps])"
COPY engine ./
COPY tools/setup-fixture.py /usr/local/lib/morphojudge-setup-fixture.py
RUN useradd --system --uid 10001 --home /nonexistent --shell /usr/sbin/nologin morpho \
 && chmod -R a+rX /engine \
 && mkdir -p /data && chown morpho:morpho /data
USER morpho
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=10s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"]
CMD ["python", "-m", "uvicorn", "morphojudge.main:app", "--host", "0.0.0.0", "--port", "8000"]
