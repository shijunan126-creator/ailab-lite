# PPT 模板后端

- 分支 `feat/ppt-templates-backend`，base `main@6b6825d0c27ccd7017467586c0ac4a9d30f3fd34`；本地实现，未push/PR/merge/deploy，Portal未改。
- [接口与决策](../../reviews/2026-10-24-ppt-templates-design.md)。实现模板注册/公司权限/个人配额与隔离、0028旧品牌迁移、受限Worker分析和缩略图、Run/queue冻结及Broker重置注入；生成器后续独立提交。
- 当前专项代码基线：首个后端提交前工作树；SQLite模板14通过；PG+Redis模板/迁移/品牌/集成专项38通过（2026-10-24）。这是本地专项结果，不是完整验收。
- 全量双库、相关E2E、性能数据待补；真实生产ClamAV/OSB与重建sbx-office尚未验。
- 部署需要迁移0028及重建sbx-office；本子任务禁止部署，移交父任务。
