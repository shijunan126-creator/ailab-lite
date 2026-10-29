# 本轮命令与脚本

执行目录为本工作树。`evidence` 为 `/tmp/ailab-real-verify-20261023`，共享材料为 `/Users/michael/.cache/ailab-real-verify-20261023`。不读取开发 `.env`；所有测试指定一次性服务。脚本中的绝对路径保留本轮执行位置，复验时需调整。

## 准备

```bash
git switch -c verify/real-components feat/arch-hardening
brew install kubectl
colima start ailab-verify --cpu 2 --memory 8 --disk 60 --kubernetes --activate=false
cd platform
uv venv -p 3.12 .venv
uv pip install -p .venv/bin/python -c constraints.txt -e '.[dev,postgres,e2e]'
uv pip install -p .venv/bin/python -c constraints.txt opensandbox==1.1.1
export PATH="$(brew --prefix coreutils)/libexec/gnubin:$(brew --prefix findutils)/libexec/gnubin:$PATH"
.venv/bin/ruff check .
.venv/bin/ruff format --check .
cd portal
npm ci
npm test
npm run build
```

## 容器与真实契约

```bash
docker --context colima-ailab-verify run -d --name verify-postgres -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_USER=ailab -e POSTGRES_DB=ailab_test -p 127.0.0.1:15450:5432 postgres:16
docker --context colima-ailab-verify exec verify-postgres createdb -U ailab ailab_migrations
docker --context colima-ailab-verify run -d --name verify-redis -p 127.0.0.1:16450:6379 redis:7-alpine
curl -fL -o /tmp/ailab-real-verify-20261023/minio-source.tar.gz https://codeload.github.com/minio/minio/tar.gz/refs/tags/RELEASE.2025-04-22T22-12-26Z
tar -xzf /tmp/ailab-real-verify-20261023/minio-source.tar.gz -C /tmp/ailab-real-verify-20261023
# Dockerfile.minio in this directory was placed in that extracted source tree as Dockerfile.verify.
docker --context colima-ailab-verify build -f /tmp/ailab-real-verify-20261023/minio-RELEASE.2025-04-22T22-12-26Z/Dockerfile.verify -t ailab-verify-minio:20250422 /tmp/ailab-real-verify-20261023/minio-RELEASE.2025-04-22T22-12-26Z
docker --context colima-ailab-verify run -d --name verify-minio -p 127.0.0.1:19450:9000 -e MINIO_ROOT_USER=verify-access -e MINIO_ROOT_PASSWORD=verify-disposable-only ailab-verify-minio:20250422 server /data
docker --context colima-ailab-verify pull --platform linux/arm64 clamav/clamav-debian:latest
docker --context colima-ailab-verify manifest inspect clamav/clamav-debian:latest
# Profile-only kubectl wrapper below; native scanner is deployed in k3s.
/tmp/ailab-real-verify-20261023/kubectl -n platform port-forward service/platform-clamav 13311:3310
cd platform
AILAB_TEST_CLAMD=tcp://127.0.0.1:13311 .venv/bin/pytest tests/contracts/test_clamd.py -ra
AILAB_TEST_S3_URL=http://127.0.0.1:19450 AILAB_TEST_S3_ACCESS_KEY=verify-access AILAB_TEST_S3_SECRET_KEY=verify-disposable-only .venv/bin/pytest tests/contracts/test_storage.py -ra
DOCKER_CONTEXT=colima-ailab-verify AILAB_TEST_DOCKER=1 .venv/bin/pytest tests/contracts/test_sandbox_lifecycle.py -k docker -ra
AILAB_TEST_OSB_URL=http://127.0.0.1:8090 .venv/bin/pytest tests/contracts/test_sandbox_lifecycle.py -k opensandbox -ra
DOCKER_CONTEXT=colima-ailab-verify AILAB_TEST_DOCKER=1 AILAB_TEST_OSB_URL=http://127.0.0.1:8090 AILAB_TEST_REDIS_URL=redis://127.0.0.1:16450/15 .venv/bin/pytest tests/faults/test_real_broker_recovery.py -ra
AILAB_TEST_OIDC_ISSUER=http://127.0.0.1:18080/realms/ailab-verify AILAB_TEST_OIDC_USERNAME=verify-user AILAB_TEST_OIDC_PASSWORD=disposable-verify-only .venv/bin/pytest tests/contracts/test_keycloak_identity.py -ra
```

Keycloak 容器：`quay.io/keycloak/keycloak:26.6 start-dev --import-realm`，768 MiB 限额，18080→8080。realm import 文件必须命名 `ailab-verify-realm.json`（realm=ailab-verify），public client=ailab-test，redirect=http://127.0.0.1:14770/*，groups mapper 输出 `/qa`。用户凭据仅一次性 fixture。Portal 使用生产构建，`AILAB_API_BASE=http://127.0.0.1:14880 npm start -- --port 14770 --hostname 127.0.0.1`；独立 API 指向任务临时 SQLite，配置 auth_backend=both、role_mapping `/qa→dept_admin,auditor`、department_mapping `/qa→verify-dept`。`identity_browser.py` 实际验收该链路。服务凭据随机生成，没有复制到本目录。

## 镜像、Office 与 OSB

```bash
docker --context colima-ailab-verify build -f platform/deploy/Dockerfile -t ailab-platform:lite .
docker --context colima-ailab-verify build -t harbor.ailab.local/sandbox/sbx-office:lite images/sbx-office
docker --context colima-ailab-verify run --rm --name verify-office-smoke --network none --memory 1536m --cpus 1 harbor.ailab.local/sandbox/sbx-office:lite sh /opt/ailab-tools/smoke/smoke.sh
colima ssh --profile ailab-verify -- sudo apt-get update
colima ssh --profile ailab-verify -- sudo apt-get install -y python3-venv
colima ssh --profile ailab-verify -- sh -c 'python3 -m venv /tmp/ailab-verify-osb-venv && /tmp/ailab-verify-osb-venv/bin/pip install opensandbox-server==1.1.1'
colima ssh --profile ailab-verify -- env OSB_HOME=/tmp/ailab-verify-osb OSB_VENV=/tmp/ailab-verify-osb-venv bash /Users/michael/.t3/worktrees/ailab-lite/t3-4a3b78af/platform/scripts/osb_up.sh
# cjk source fixtures and render_preview.py were copied into the shared cjk directory.
docker --context colima-ailab-verify run --rm --name verify-cjk --network none --memory 768m --cpus 1 -v /Users/michael/.cache/ailab-real-verify-20261023/cjk:/workspace/cjk harbor.ailab.local/sandbox/sbx-office:lite sh -c 'python /workspace/cjk/render_preview.py /workspace/cjk/chinese.docx /workspace/cjk/docx; python /workspace/cjk/render_preview.py /workspace/cjk/chinese.pptx /workspace/cjk/pptx'
```

平台修复采用保留构建状态的增量镜像 `Dockerfile.patched`、`Dockerfile.queue`；最终 api/agent/worker 使用 ailab-platform:verify-health，broker 使用 ailab-platform:verify。与本地提交的对应源码一致。

## k3s

`/tmp/ailab-real-verify-20261023/kubectl` 的内容仅为：

```sh
#!/bin/sh
exec /opt/homebrew/bin/colima ssh --profile ailab-verify -- sudo k3s kubectl "$@"
```

KUBECONFIG 是从该 VM `/etc/rancher/k3s/k3s.yaml` 复制出的私密文件，只供 helpers 查找，不用于其他集群。

```bash
KUBECONFIG=/tmp/ailab-real-verify-20261023/kubeconfig KUBECTL=/tmp/ailab-real-verify-20261023/kubectl bash platform/scripts/k8s_secrets.sh
KUBECONFIG=/tmp/ailab-real-verify-20261023/kubeconfig KUBECTL=/tmp/ailab-real-verify-20261023/kubectl MODELS_FILE=/Users/michael/.cache/ailab-real-verify-20261023/models.toml NO_RESTART=1 bash platform/scripts/k8s_models.sh
kubectl kustomize platform/deploy/overlays/lite > /tmp/ailab-real-verify-20261023/lite.yaml
/tmp/ailab-real-verify-20261023/kubectl apply -f - < /tmp/ailab-real-verify-20261023/lite.yaml
/tmp/ailab-real-verify-20261023/kubectl -n platform get pods
# Only [[models]] id=ailab-demo, provider=mock, default=true was present in models.toml.
bash /tmp/ailab-real-verify-20261023/deploy-smoke.sh
```

`deploy-smoke.sh` 是 deploy.sh 原 smoke_test 函数，WITH_PORTAL=0，API_URL=127.0.0.1:30080；身份通过 Secret 读取到内存，不输出。网络、扫描暂停、Work/预览脚本均见同目录原样 .py。服务 token 的私密 JSON 输入没有提交。

## 全套 Linux PG16/Redis/S3/原生 ClamAV

先按 `Dockerfile.tests` 构建 `ailab-verify-tests:20261023`，再用 `Dockerfile.pg16` 安装匹配 PostgreSQL 16 的客户端构建 `ailab-verify-tests:pg16`。创建一次性 bucket `verify-full-suite`。最后一次完整命令：

```bash
docker --context colima-ailab-verify run --rm --name verify-linux-final-health --network host \
  -e AILAB_STORAGE_BACKEND=s3 \
  -e AILAB_S3_ENDPOINT=http://127.0.0.1:19450 \
  -e AILAB_S3_ACCESS_KEY=verify-access -e AILAB_S3_SECRET_KEY=verify-disposable-only \
  -e AILAB_S3_BUCKET=verify-full-suite \
  -e AILAB_TEST_REDIS_URL=redis://127.0.0.1:16450/15 \
  -e AILAB_TEST_DATABASE_URL=postgresql+psycopg://ailab@127.0.0.1:15450/ailab_test \
  -e AILAB_TEST_POSTGRES_URL=postgresql+psycopg://ailab@127.0.0.1:15450/ailab_migrations \
  -e AILAB_TEST_S3_URL=http://127.0.0.1:19450 \
  -e AILAB_TEST_S3_ACCESS_KEY=verify-access -e AILAB_TEST_S3_SECRET_KEY=verify-disposable-only \
  -e AILAB_TEST_CLAMD=tcp://127.0.0.1:13311 \
  -v "$PWD:/repo:ro" -v /Users/michael/.cache/ailab-real-verify-20261023:/evidence \
  -w /repo/platform ailab-verify-tests:pg16 pytest tests -ra \
  -o cache_dir=/tmp/pytest-cache --junitxml=/evidence/linux-final-health.xml
```

753 passed / 9 skipped / 101.26 s。中间 Local 全局存储配置 + S3 契约全套 751/10/99.38 s；全局 S3 751/10/94.24 s；增加原生 clamd gate 752/9/100.16 s；最后增加健康探测回归为753/9。

## 资源与恢复

```bash
platform/.venv/bin/python /Users/michael/.cache/ailab-real-verify-20261023/make_fixtures.py
colima ssh --profile ailab-verify -- sudo python3 /Users/michael/.cache/ailab-real-verify-20261023/sample_resources.py
platform/.venv/bin/python /Users/michael/.cache/ailab-real-verify-20261023/resource_workloads.py
platform/.venv/bin/python /Users/michael/.cache/ailab-real-verify-20261023/backup_drill.py
platform/.venv/bin/python /Users/michael/.cache/ailab-real-verify-20261023/restored_smoke.py
```

sampler 与 workload 分别运行；pod-map.json 由该 context 的 docker inspect 获取 PID/limit，phase 用共享文件切换。backup_drill.py 在真实 API 数据卷只读挂载的探针中调用 `ops.backup.create/verify/restore/verify`，写目标为空。原平台四个写服务暂停期间备份，finally 恢复；restore smoke 使用新容器与空目录恢复的副本，不覆盖原 PVC。

## 清理

仅清理专用 profile 内本轮 namespace、测试容器、匿名/指定签名卷与本轮自建镜像，保留上游基础镜像；停止 `colima stop ailab-verify`，不删除 profile。清理结论见上级验收报告。本地证据/备份保留供 lead 复查；私密 token/kubeconfig 临时副本在结束时删除。
