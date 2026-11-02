# Single image for all four Python platform services.
# Build (from the repo root, so the skills under images/ are in the context):
#         docker build -f platform/deploy/Dockerfile -t ailab-platform:lite .
# Run:    docker run --rm -p 8080:8080 ailab-platform:lite
#         (override command with e.g. `uvicorn ailab_platform.broker.main:app ...`)
FROM node:24.11.1-bookworm-slim AS viewer
WORKDIR /build
COPY platform/viewer-runtime/package.json platform/viewer-runtime/package-lock.json ./
RUN npm ci --ignore-scripts
COPY vendor/office-suite/upstream/office-runtime ./vendor/office-suite/upstream/office-runtime
COPY platform/ailab_platform/sandbox_tools/viewer-reader.ts ./platform/ailab_platform/sandbox_tools/viewer-reader.ts
RUN node_modules/.bin/esbuild platform/ailab_platform/sandbox_tools/viewer-reader.ts \
    --bundle --platform=node --format=esm --packages=external --outfile=/build/reader.mjs \
 && npm prune --omit=dev --ignore-scripts

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 && rm -rf /var/lib/apt/lists/*
COPY --from=viewer /usr/local/bin/node /usr/local/bin/node
COPY --from=viewer /build/reader.mjs /opt/ailab-viewer/reader.mjs
COPY vendor/office-suite/upstream/office-runtime/LICENSE /opt/ailab-viewer/LICENSE
COPY --from=viewer /build/node_modules /opt/ailab-viewer/node_modules

WORKDIR /app

COPY platform/pyproject.toml platform/constraints.txt platform/README.md ./
COPY platform/ailab_platform ./ailab_platform
COPY images/sbx-office/skills /opt/ailab/skills
COPY platform/models.toml /etc/ailab/models.toml
RUN pip install --upgrade pip \
 && pip install -c constraints.txt ".[postgres]" \
 && pip install -c constraints.txt "opensandbox==1.1.1"

RUN useradd -u 10001 -m appuser \
 && mkdir -p /data \
 && chown -R 10001:10001 /data /app
USER 10001

ENV AILAB_SKILLS_DIR=/opt/ailab/skills \
    AILAB_MODELS_FILE=/etc/ailab/models.toml \
    AILAB_DATA_DIR=/data \
    AILAB_STORAGE_ROOT=/data/storage \
    AILAB_DATABASE_URL=sqlite:////data/platform.db

EXPOSE 8080 8081 8082 8083
CMD ["uvicorn", "ailab_platform.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
