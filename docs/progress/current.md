# 当前工作

更新：2026-10-24。用户授权：Portal 对话模式状态头改为思考语义并提 PR，CI 通过后合并。此前 k3s/OpenSandbox 受控联网修复（PR #36）已合并；外盘迁移已完成，个人路径和运行环境只见私有 HANDOFF。

## 已完成

- 既有受控联网PR #33和README已在main；k3s/OpenSandbox修复PR #36已审查、CI全绿并合并为main `2ed270f`；当前Compose部署不受影响。
- 对话模式状态头不再显示“工作总时长”：进行中“正在思考…”，结束“已思考 N秒”，过程条目与无障碍名称为“思考”；工作模式不变（分支 `fix/chat-thinking-heading`）。
- 本分支补齐k3s节点runsc 4GiB根overlay、namespace ephemeral-storage、OSB显式Kubernetes断网模式/secure门禁、kube-router ICMP缺口、非root注入目录；部署核对runtime/template/进程配置并导入sandbox镜像。
- 独立3CPU/5GiB ARM64 k3s/OpenSandbox/gVisor已完成契约验收，临时VM、目录、PG/Redis与卷已清理，外盘回收约10.01GiB。详见[本轮更新](updates/2026-10-24-k3s-osb-network.md)、[详细新旧证据](../reviews/2026-10-24-controlled-network.md)、[ADR0007](../architecture/adr/0007-controlled-public-fetch.md)。

## 分支、基线与验证

- `fix/k3s-osb-network-path`，base `main@1a384558b4ac27deea660e5314d82eddf462b386`；Claude原有review补充已保留，三个未跟踪`.DS_Store`未动。
- 核心双库：SQLite968 passed/67 skipped，PG16+Redis1003/33 skipped；E2E61（包含Portal测试/build）；真实Docker/OSB传输+生命周期16、联网综合2、网络各通过；仓库生成OSB配置复验3。最后进程/启动配置绑定由专项5及真实Linux启动/复用/配置变更拒绝验证。完整细节和代码指纹见详细报告。
- 平台及新增脚本Ruff、ShellCheck、bash、双overlay和diff检查通过；无依赖或Portal源码变更。额外全仓Ruff28项历史问题未修。

## 剩余与下一步

1. 已审查合并PR #36。保留的假设：固定IPv4单节点/CNI、节点ICMP规则影响所有Pod诊断ping、secure隔离开关是一次性人工确认而非持续attestation。
2. 完整五服务k3s/Office/真实模型/真实技能保存扫描端到端、IPv6、多节点/其他CNI及长期并发仍未验；不自动开始这些验收或其他路线图任务。
3. 临时VM期间外盘USB物理掉线导致现有VM卡住；已强制停止并重启，9容器健康、Portal 200，完整性核对原有ID全部保留、文件哈希正确。外盘意外掉线后的自动清理/恢复尚未实现（需用户决定是否做真实断盘验证），详见私有HANDOFF。
