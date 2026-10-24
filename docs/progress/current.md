# 当前工作

更新：2026-10-24。当前授权是k3s/OpenSandbox受控联网实现子任务：修复、真实验证、提交到分支、推送并创建PR；**不合并**，由协调者审查CI后决定。此前外盘迁移已完成；个人路径和运行环境只见私有HANDOFF。

## 已完成

- 既有受控联网PR #33和README已在main；当前Compose部署不变。
- 本分支补齐k3s节点runsc 4GiB根overlay、namespace ephemeral-storage、OSB显式Kubernetes断网模式/secure门禁、kube-router ICMP缺口、非root注入目录；部署核对runtime/template/进程配置并导入sandbox镜像。
- 独立3CPU/5GiB ARM64 k3s/OpenSandbox/gVisor已完成契约验收，临时VM、目录、PG/Redis与卷已清理，外盘回收约10.01GiB。详见[本轮更新](updates/2026-10-24-k3s-osb-network.md)、[详细新旧证据](../reviews/2026-10-24-controlled-network.md)、[ADR0007](../architecture/adr/0007-controlled-public-fetch.md)。

## 分支、基线与验证

- `fix/k3s-osb-network-path`，base `main@1a384558b4ac27deea660e5314d82eddf462b386`；Claude原有review补充已保留，三个未跟踪`.DS_Store`未动。
- 核心双库：SQLite968 passed/67 skipped，PG16+Redis1003/33 skipped；E2E61（包含Portal测试/build）；真实Docker/OSB传输+生命周期16、联网综合2、网络各通过；仓库生成OSB配置复验3。最后进程/启动配置绑定由专项5及真实Linux启动/复用/配置变更拒绝验证。完整细节和代码指纹见详细报告。
- 平台及新增脚本Ruff、ShellCheck、bash、双overlay和diff检查通过；无依赖或Portal源码变更。额外全仓Ruff28项历史问题未修。

## 剩余与下一步

1. 协调者审查本分支PR及CI，重点看固定IPv4单节点/CNI假设、节点ICMP规则影响所有Pod诊断ping、secure隔离声明不等于持续attestation。实现子任务不合并。
2. 完整五服务k3s/Office/真实模型/真实技能保存扫描端到端、IPv6、多节点/其他CNI及长期并发仍未验；不自动开始这些验收或其他路线图任务。
3. 临时VM期间外盘卸载/重挂导致现有VM管理socket异常。现有Portal仍200，但profile管理状态Broken；本轮未重启/修复现有VM，不据Portal200声称全部服务或数据健康。协调者先查看私有HANDOFF再决定维护动作。
