# sbx-office:lite — 精简版沙箱镜像，结构对齐 AILab 文档 08 的 L0～L6 分层
# Chromium、TeX 仅按需运行；无常驻 Office/Node/浏览器服务。
# 必须 buildx 双架构构建（amd64 + arm64），见 tests/t1_image.sh。
# Build from the repository root; use the same pinned parser as the system reader.
FROM node:24.11.1-bookworm-slim@sha256:48abc13a19400ca3985071e287bd405a1d99306770eb81d61202fb6b65cf0b57 AS runtime
WORKDIR /opt/ailab-office
COPY vendor/office-suite/runtime/package.json vendor/office-suite/runtime/package-lock.json ./
RUN npm ci --ignore-scripts
COPY vendor/office-suite/runtime/src ./src
COPY vendor/office-suite/runtime/runner.ts vendor/office-suite/runtime/LICENSE vendor/office-suite/runtime/APACHE-2.0 vendor/office-suite/runtime/UPSTREAM.json ./
RUN node_modules/.bin/esbuild runner.ts --bundle --platform=node --format=esm --packages=external --outfile=runner.mjs \
 && npm prune --omit=dev --ignore-scripts \
 && sha256sum runner.mjs > runtime.sha256

FROM node:24.11.1-bookworm-slim@sha256:48abc13a19400ca3985071e287bd405a1d99306770eb81d61202fb6b65cf0b57 AS production-node
WORKDIR /opt/office-production
COPY images/sbx-office/package.json images/sbx-office/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force

FROM python:3.12-slim-trixie@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

# Snapshot fixes the complete system/font/TeX dependency closure, independently of skill changes.
ARG DEBIAN_SNAPSHOT=20261101T000000Z
RUN rm -f /etc/apt/sources.list.d/* \
 && echo "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}/ trixie main" > /etc/apt/sources.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-impress-nogui libreoffice-writer-nogui libreoffice-calc-nogui \
      poppler-utils fonts-noto-cjk fonts-noto-core fonts-wqy-zenhei fontconfig tini \
      libatomic1 libstdc++6 python3-uno zip unzip chromium pandoc imagemagick tesseract-ocr \
      texlive-xetex texlive-latex-extra texlive-science texlive-lang-chinese texlive-fonts-recommended \
 && rm -rf /var/lib/apt/lists/*

# Python closure is fully pinned and hash verified; changes do not invalidate the larger system layer.
COPY images/sbx-office/requirements.lock /opt/office-production/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /opt/office-production/requirements.lock
COPY --from=production-node /opt/office-production /opt/office-production
COPY --from=runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=runtime /opt/ailab-office /opt/ailab-office
ENV NODE_PATH=/opt/office-production/node_modules:/opt/ailab-office/node_modules \
    PLAYWRIGHT_CHROMIUM_PATH=/usr/bin/chromium PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

COPY images/sbx-office/fonts-local.conf /etc/fonts/local.conf
RUN fc-cache -f
COPY images/sbx-office/office-read /usr/local/bin/office-read
COPY images/sbx-office/skills/ /opt/skills/
COPY images/sbx-office/skill_index.py /opt/ailab-tools/skill_index.py
RUN python /opt/ailab-tools/skill_index.py /opt/skills --check --requirements
COPY images/sbx-office/smoke/ /opt/ailab-tools/smoke/

RUN useradd -u 10001 -m -d /home/sandbox sandbox \
 && mkdir -p /workspace/inputs /workspace/outputs \
 && chown -R 10001:10001 /workspace
USER 10001
WORKDIR /workspace
ENV HOME=/home/sandbox PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sleep", "infinity"]
