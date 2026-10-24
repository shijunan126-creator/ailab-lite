# k3s / OpenSandbox 受控联网修复与验收

授权：作为实现子任务，从main分支实现、验证、提交、推送和开PR；不合并，交协调者审查CI。

分支 `fix/k3s-osb-network-path`，base `main@1a384558b4ac27deea660e5314d82eddf462b386`。保留Claude原有并发/IPv6 review改动，三个未跟踪`.DS_Store`未动。代码基线与详细新旧证据区分见[专项报告](../../reviews/2026-10-24-controlled-network.md)。

本轮完成：节点runsc 4GiB根overlay和namespace ephemeral-storage约束；OSB显式Kubernetes断网模式与secure验收门禁；修补kube-router先放行ICMP的实际出口；非root用户预建上传父目录；deploy核对runtime/template/进程及配置、导入sandbox镜像。决策更新[ADR0007](../../architecture/adr/0007-controlled-public-fetch.md)。

验证：核心改动SQLite968/67 skipped、PG16+Redis1003/33 skipped；E2E61（含Portal测试/构建）；真实Docker/OSB传输生命周期16、公开获取2、网络2，以及生成部署配置后的OSB传输/网络3；最后新增进程helper专项5与真实Linux启动/配置变更拒绝。平台/新增脚本Ruff、ShellCheck、bash、双overlay和diff检查通过。没有依赖变更；额外全仓Ruff28项历史问题未修。

边界：临时3CPU/5GiB ARM64 VM中的契约验收，不是完整五服务/Office/真实模型/真实技能保存扫描端到端；IPv6/多节点/其他CNI/长期并发未验。节点ICMP规则影响整个固定IPv4 Pod CIDR的诊断ping；mounted writable volumes不受根overlay上限约束。现有Compose没有部署切换或重启；外盘卸载/重挂造成的现有VM管理通路问题留给协调者处理，不能只由Portal200推断整体健康。个人路径和故障细节仅记录私有HANDOFF。

下一步：协调者审查PR、CI与固定IPv4部署约束，再决定合并或完整平台验收；本子任务不自动部署现有运行环境、不合并。

清理：临时PG/Redis、独立VM/目录已删除，无遗留任务进程/测试端口；外盘df可用增加约10.01GiB。原始日志留在私有运行交接，仓库不包含个人配置。
