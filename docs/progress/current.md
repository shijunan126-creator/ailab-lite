# 当前工作

## Work 标配 Office Runtime（2026-10-29）

用户已授权部署、提PR及合并；[PR #106](https://github.com/shijunan126-creator/ailab-lite/pull/106) 等待CI。独立工作树 `.worktrees/work-office-runtime`，分支 `feat/work-office-runtime`，基线 `origin/main@09a28e8`；主目录的设计、证据清理及其他工作树修改保留。

已预装与系统 reader 同版本的 Node/解析核心，增加按需 `office-read` 命令、内部 skill 指令、四格式/修改后重读镜像合同，更新构建上下文与缓存失效输入。平台系统读取通道不变。设计与性能边界见 [ADR 0025](../architecture/adr/0025-work-office-reader.md)，检查结果见[本轮记录](updates/2026-10-29-work-office-runtime.md)。

已集中修正Chat加载Work命令、Work主提示词缺指导和输出目录竞争，完成真实runsc镜像合同及DeepSeek Flash读取→编辑→重读验证；详见同一实施记录新增章节。固定产品提交467971b已增量部署本机Agent与Work镜像，Broker配置更新，compose启动完成；按用户约定未额外做部署后验收。下一步等待PR检查通过后合并。既有 NPU、多代满窗和数百人容量边界不变；前端 HLD 仍仅设计。
