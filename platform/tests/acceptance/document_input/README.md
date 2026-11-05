# 文件输入真实验收

样本生成器仅生成合成数据，不使用用户文件。二进制、原始采样、heap和trace留在仓库外；`gates.json`是测试前冻结的机器可读验收合同，不是运行输出。修改门槛不得用来消除已观察到的失败。

在新的任务临时目录生成两次，核对manifest/逐文件SHA一致后只保留一份：

```sh
platform/.venv/bin/python platform/tests/acceptance/document_input/samples.py /private/tmp/<owned>/samples-a
platform/.venv/bin/python platform/tests/acceptance/document_input/samples.py /private/tmp/<owned>/samples-b
```

真实native组件基线使用已有Office镜像的不可变image ID，向**新建**的无网络、只读rootfs、1 CPU/768MiB容器流式注入当前runner与合成样本；V8 heap160MiB、Node RSS320MiB、解析deadline60秒。每次新进程/输出目录；首进程与重复解析分别统计，重复解析不是派生缓存命中，不能称OS磁盘冷启动。结束在finally删除自己的容器。不修改Docker context或既有服务、卷、凭据。完整端到端/浏览器/模型与故障验收仍需独立隔离栈，不能用此脚本代替。

```sh
platform/.venv/bin/python platform/tests/acceptance/document_input/native_benchmark.py /private/tmp/<owned> \
  --context <available-context> --image <pinned-office-image> --repeats 5
```

组件基线、设备和未测边界统一记在[实施记录](../../../../docs/progress/updates/2026-11-05-document-input-lifecycle.md)，不提交生成的manifest或原始采样。当前二进制重现需要可用的中文系统字体；缺失时明确失败，不以方框替代中文字。
