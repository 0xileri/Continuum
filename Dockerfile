# Continuum — single-service deployment.
#
# One container serves the compiled dashboard and the read API together. Two services would need
# CORS, a second URL and a way for the frontend to discover the backend; one service needs none of
# that, and the frontend's relative base URL works unchanged.
#
# The deployed instance is a READ-ONLY showcase of the published record. It carries no signing key,
# makes no model calls, and its only write endpoint is closed by CONTINUUM_API_READ_ONLY. See the
# environment block at the bottom.

# ---- stage 1: build the dashboard ----------------------------------------------------
FROM node:22-slim AS dashboard

WORKDIR /build
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY dashboard/ ./
RUN npm run build


# ---- stage 2: the API, serving that build --------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Only the read path's dependencies. lightgbm, scikit-learn, anthropic and the parquet engines are
# all absent deliberately: a read-only instance never recomputes a score, so it never loads a
# scorer, calls a model, or opens a raw event table. That keeps the image small and, more usefully,
# means the deployed surface cannot do those things even if a future endpoint tried to.
RUN pip install --no-cache-dir \
        "fastapi>=0.115,<1" \
        "uvicorn>=0.32,<1" \
        "pydantic>=2.10,<3" \
        "python-dotenv>=1.0,<2"

# The commit this image was built from, so a running container can say what it is. Without it
# "is my fix deployed?" can only be answered by guessing from asset hashes, which is how a stale
# deploy goes unnoticed.
ARG GIT_COMMIT=unknown
ENV CONTINUUM_BUILD_COMMIT=$GIT_COMMIT

COPY continuum/ ./continuum/
COPY deployments/ ./deployments/
# Only the sanitised export — see scripts/export_public_data.py. The generator's raw event
# tables and its ground-truth keys never enter the image.
COPY data/public/ ./data/public/
COPY --from=dashboard /build/dist ./dashboard/dist

# Read-only by construction, not just by configuration. Even if CONTINUUM_API_READ_ONLY were
# unset, the dispute path would fail on its missing imports rather than spend anything.
ENV CONTINUUM_DATA_DIR=/app/data/public \
    CONTINUUM_API_READ_ONLY=1 \
    CONTINUUM_API_ALLOW_REMOTE=1 \
    CONTINUUM_LLM_BACKEND=offline \
    CONTINUUM_OG_NETWORK=mainnet \
    CONTINUUM_OG_PUBLISH=0 \
    CONTINUUM_SCORER=quant

EXPOSE 8000

# Railway supplies $PORT; default to 8000 so the image also runs locally with plain `docker run`.
CMD ["sh", "-c", "uvicorn continuum.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
