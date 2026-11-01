# sbx-office:lite — 精简版沙箱镜像，结构对齐 AILab 文档 08 的 L0～L6 分层
# 不装 Chromium；Office reader 按需启动，不常驻 Node 进程。
# 必须 buildx 双架构构建（amd64 + arm64），见 tests/t1_image.sh。
# Build from the repository root; use the same pinned parser as the system reader.
FROM node:24.11.1-bookworm-slim AS runtime
WORKDIR /opt/ailab-office
COPY vendor/zcode-office-runtime/package.json vendor/zcode-office-runtime/package-lock.json ./
RUN npm ci --ignore-scripts
COPY vendor/zcode-office-runtime/src ./src
COPY vendor/zcode-office-runtime/runner.ts vendor/zcode-office-runtime/LICENSE vendor/zcode-office-runtime/APACHE-2.0 vendor/zcode-office-runtime/UPSTREAM.json ./
RUN node_modules/.bin/esbuild runner.ts --bundle --platform=node --format=esm --packages=external --outfile=runner.mjs \
 && npm prune --omit=dev --ignore-scripts \
 && sha256sum runner.mjs > runtime.sha256

FROM python:3.12-slim-trixie

# L1 系统工具 + L5 中文字体
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-impress-nogui libreoffice-writer-nogui libreoffice-calc-nogui \
      poppler-utils fonts-noto-cjk fontconfig tini libatomic1 libstdc++6 python3-uno zip unzip \
 && rm -rf /var/lib/apt/lists/*

# L3 Python 办公库（版本锁定）
RUN pip install --no-cache-dir \
      python-pptx==1.0.2 python-docx==1.2.0 openpyxl==3.1.5 \
      matplotlib==3.10.7 pandas==2.3.3 pypdf==6.1.3

# 字体映射：模型和模板里写“微软雅黑/宋体/仿宋”等，实际落到 Noto CJK；zip/unzip 供技能脚本打包 docx 等
COPY images/sbx-office/fonts-local.conf /etc/fonts/local.conf
RUN fc-cache -f

# 同一固定读取核心供工作区检查使用；平台权威索引仍走系统 reader。
COPY --from=runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=runtime /opt/ailab-office/package.json /opt/ailab-office/runner.mjs /opt/ailab-office/runtime.sha256 /opt/ailab-office/LICENSE /opt/ailab-office/APACHE-2.0 /opt/ailab-office/UPSTREAM.json /opt/ailab-office/
COPY --from=runtime /opt/ailab-office/node_modules /opt/ailab-office/node_modules
COPY images/sbx-office/office-read /usr/local/bin/office-read

# L6 Skills 与工具
COPY images/sbx-office/skills/ /opt/skills/
COPY images/sbx-office/skill_index.py /opt/ailab-tools/skill_index.py
RUN python /opt/ailab-tools/skill_index.py /opt/skills --check --requirements
COPY images/sbx-office/smoke/ /opt/ailab-tools/smoke/

# 运行时约定：非 root、/workspace/{inputs,outputs}
RUN useradd -u 10001 -m -d /home/sandbox sandbox \
 && mkdir -p /workspace/inputs /workspace/outputs \
 && chown -R 10001:10001 /workspace
USER 10001
WORKDIR /workspace
ENV HOME=/home/sandbox PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sleep", "infinity"]
