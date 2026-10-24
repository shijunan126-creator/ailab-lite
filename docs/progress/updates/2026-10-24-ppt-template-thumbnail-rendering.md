# PPT 模板真实首屏缩略图

- 授权：父任务要求复用真实渲染器作缩略图，草图仅fallback；在 `feat/ppt-templates-backend` 继续，本轮起点 `833f64b`。不改Portal、不push/PR/merge/deploy。
- 共享 `sandbox_tools/render_preview.pages`，仅导出PDF第一页并光栅化为宽480px JPEG；独立受限进程运行。外层超时/内存/子进程失败后在独立进程作草图，保留ready；所有缩略图都失败则不返回thumbnail_url，但不损害已成功分析的模板。
- 原预算：默认192MiB、配置上限256MiB未提高；渲染51秒/草图9秒，CPU45秒、递归输出16MiB；渲染整进程组RSS watchdog，普通解析原RLIMIT_AS保留。LO profile/PDF在任务目录，杀进程后由父目录生命周期清理。已有v2对象和ready状态避免重复渲染，旧v1 WebP继续可读。
- 镜像：`platform/deploy/Dockerfile`补LibreOffice Impress、Poppler、CJK字体、fontconfig和procps。这会增加共享Python镜像磁盘，尚未有Docker构建大小证据；不运行LO时没有常驻渲染进程。已有库PPT预览实际走Broker，本轮复用其renderer，不修改DB/队列。
- 当前专项：51通过/1跳过（PG迁移环境未设置）。全量SQLite与最终成本待补；不得把初版验收数字当本轮结果。
- 首次实测：macOS默认192MiB触发内存回退；在原有256MiB配置范围内能生成真实JPEG。20页探针256MiB三次只有一次成功、两次触限，须如实保留，不能声称所有模板在该预算都能真实渲染。
