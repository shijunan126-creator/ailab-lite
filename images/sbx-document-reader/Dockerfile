# Build from the repository root. Dedicated system reader, never a user's Work lease.
FROM node:24.11.1-bookworm-slim AS runtime
WORKDIR /opt/ailab-office
COPY vendor/zcode-office-runtime/package.json vendor/zcode-office-runtime/package-lock.json ./
RUN npm ci --ignore-scripts
COPY vendor/zcode-office-runtime/src ./src
COPY vendor/zcode-office-runtime/runner.ts vendor/zcode-office-runtime/LICENSE vendor/zcode-office-runtime/APACHE-2.0 vendor/zcode-office-runtime/UPSTREAM.json ./
RUN node_modules/.bin/esbuild runner.ts --bundle --platform=node --format=esm --packages=external --outfile=runner.mjs \
 && npm prune --omit=dev --ignore-scripts \
 && sha256sum runner.mjs > runtime.sha256

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 libstdc++6 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=runtime /opt/ailab-office/package.json /opt/ailab-office/runner.mjs /opt/ailab-office/runtime.sha256 /opt/ailab-office/LICENSE /opt/ailab-office/APACHE-2.0 /opt/ailab-office/UPSTREAM.json /opt/ailab-office/
COPY --from=runtime /opt/ailab-office/node_modules /opt/ailab-office/node_modules
RUN useradd -u 10001 -m sandbox \
 && mkdir -p /workspace/inputs /workspace/outputs /workspace/.ailab \
 && chown -R 10001:10001 /workspace
USER 10001
WORKDIR /workspace
CMD ["sleep","infinity"]
