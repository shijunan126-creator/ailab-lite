# 文档输入验收

这里生成合成语料，测量真实解析、上传、预览、浏览器稳定性与真模型工作流。它不启动生产环境，也不把脚本成功当成完整产品验收。原始二进制、日志和采样只放在仓库外的专属临时目录，验收结束后删除。

`gates.json` 是冻结的验收门槛。失败后修根因或如实报告，不能降低门槛来消除已观察到的失败。

## 合成语料

从仓库根运行两次，确认 `manifest.json` 和逐文件 SHA 一致后只保留一份：

```bash
platform/.venv/bin/python platform/tests/acceptance/document_input/samples.py /tmp/<owned>/samples-a
platform/.venv/bin/python platform/tests/acceptance/document_input/samples.py /tmp/<owned>/samples-b
```

生成器需要中文系统字体；缺字体时明确失败，不能用缺字方框替代中文字。语料覆盖多页/长 PDF、扫描页、混合 DOCX/PPTX、XLSX 与图片，不作为用户文件使用。

## 原生解析基线

`native_benchmark.py` 在新建的无网络、只读 rootfs、1 CPU/768 MiB 容器中运行当前 Office runner。每个样本使用新进程和输出目录；后续重复是操作系统缓存复测，不是派生缓存命中，也不能称为保证冷启动。

```bash
platform/.venv/bin/python platform/tests/acceptance/document_input/native_benchmark.py \
  /tmp/<owned> --context <docker-context> --image <pinned-office-image> --repeats 5
```

`--profile chat` 测 Chat 原生读取，`legacy` 测默认解析。脚本在 `finally` 删除自己的容器，不修改 Docker context、卷或既有服务。

## 上传链路基线

`stack_benchmark.py` 启动自己的 API/Broker/Worker/Redis、ClamAV 容器和唯一 Broker owner，使用 313xx 端口、临时 SQLite、对象目录和随机服务身份；不读取现有业务栈或模型密钥。端口已占用时拒绝运行。

```bash
platform/.venv/bin/python platform/tests/acceptance/document_input/stack_benchmark.py \
  /tmp/<owned> --context <docker-context> --output upload-baseline.json
```

首次上传与同字节独立副本分别记录；后者可以命中私有派生缓存，不能称为第二次解析。服务 RSS 是轮询峰值，不是容器总 RSS。正常结束和异常都会停止自有进程/容器；失败时先检查打印的 state 目录，再删除整个专属目录。加 `--prepare-native --office-image <image>` 会在安全 ready 后通过 Chat 请求原生解析，并核对其实际终态。

`storage_benchmark.py` 用合成对象比较分页索引与固定基线适配器，输出到指定目录；它衡量适配器，不代表目标机或完整服务链路。

## 浏览器稳定性

完整截图、历史、分叉、删除、错误恢复与竞态验收在 `platform/e2e/test_document_input_acceptance.py`。使用可丢弃的 Docker 后端和独立 Redis，并让宿主 Worker 的 `AILAB_VIEWER_READER_PATH` 指向可解析当前 Node 依赖的 reader：

```bash
cd platform
AILAB_E2E=1 AILAB_DOCUMENT_STABILITY=1 \
AILAB_E2E_SANDBOX_PROVIDER=docker \
AILAB_E2E_SANDBOX_IMAGE=<current-office-image> \
AILAB_VIEWER_READER_PATH=<reader.mjs> \
.venv/bin/pytest e2e/test_document_input_acceptance.py -q
```

真正验证 ClamAV 时再设置 `AILAB_E2E_SCANNER_BACKEND=clamav` 与 `AILAB_E2E_CLAMD_ADDRESS`。50 轮稳定性用例使用独占资料库，不能同时向同一身份上传性能样本。保留期测试只推进测试数据库时钟，不伪造真实等待 30 天。

## 真模型工作流

`workflow_probe.py` 只连接调用者提供的隔离 API，经公开接口上传合成语料、创建会话并记录工具序列、失败调用、终态和交付件；不启动栈，也不读取模型密钥。

`--auth-file` 是权限 `0600` 的测试用户 session JSON，字段为 `token`。计划是 case 数组，每项包含 `id`、`mode`、`files`、`turns`，可选 `browser_gate` 暂停等待外部浏览器准备；准备好后创建 `<output>/<id>.continue` 放行。输出必须位于仓库外。

```bash
platform/.venv/bin/python platform/tests/acceptance/document_input/workflow_probe.py \
  --api http://127.0.0.1:<api-port> \
  --auth-file /tmp/<owned>/auth.json \
  --model <real-model-alias> \
  --plan /tmp/<owned>/plan.json \
  --corpus /tmp/<owned>/samples-a \
  --output /tmp/<owned>/workflow
```

模型别名不能证明实际调用；另核对账本的 `actual_backend` 与 `settled`。按[测试的真模型判定](../../../../docs/modules/testing.md#真模型场景)逐例检查回答、工具路线、失败原因和实际下载的交付件。重复工具事件可能是 checkpoint 重放，不能直接当作重复模型决策。API 驱动的结果也不等于所有用例都经过浏览器上传与发送。
