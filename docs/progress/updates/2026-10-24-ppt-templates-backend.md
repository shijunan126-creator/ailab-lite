# PPT 模板后端

- 分支 `feat/ppt-templates-backend`，base `main@6b6825d0c27ccd7017467586c0ac4a9d30f3fd34`；本地实现，未push/PR/merge/deploy，Portal未改。
- [接口与决策](../../reviews/2026-10-24-ppt-templates-design.md)。实现模板注册/公司权限/个人配额与隔离、0028旧品牌迁移、受限Worker分析和缩略图、Run/queue冻结及Broker重置注入；生成器后续独立提交。
- 当前专项代码基线：首个后端提交前工作树；SQLite模板14通过；PG+Redis模板/迁移/品牌/集成专项38通过（2026-10-24）。这是本地专项结果，不是完整验收。
- 全量双库、相关E2E、性能数据待补；真实生产ClamAV/OSB与重建sbx-office尚未验。
- 部署需要迁移0028及重建sbx-office；本子任务禁止部署，移交父任务。

## 跨服务与性能专项

- 五服务 E2E 新用例 `e2e/test_ppt_templates.py`：1通过，18.26秒。专用SQLite/Redis14、18480–18483/13400端口；Portal仅由既有fixture复制构建，没有修改其源码。真实API/Agent/Broker/Worker/outbox/Redis；模型和沙箱为mock，不等于真实Office镜像或生产扫描器验收。
- 验证上传→扫描/受限分析→ready与ETag、Run frozen选择→Broker文件注入→下一轮恢复工作区后.template消失。生成器另用真实python-pptx模板/POTX测试原生Title/Body/Two Content版式、示例页删除、无额外品牌logo及富内容fallback。
- `PYTHONPATH=. python -c 'import tests.conftest; from tests.measure_templates import main; main()'`：20页/44,559字节小样本，5次中位CPU0.112341s、wall0.192465s，子进程peak RSS最大45,498,368字节，输出最大5,857字节。是本机短样本，不是30MiB图像模板或2vCPU长期容量证据。无新运行依赖；分析仅一次，Pillow首屏图缓存于derived。
- 首轮全量失败如实保留：macOS缺GNU PATH导致快照/预览测试失败；另捕获无template多写空options及清库fixture遗漏templates外键。已修复兼容及fixture并按GNU PATH重跑，不放宽业务断言。
