# Chat文档视觉准备：逻辑核对与覆盖缺口

用户要求详细解释PPT/Word/PDF/Excel的输入与视觉判定；本轮只读源码/最小函数探针，未改产品、未部署、未提新PR。核对产品为PR160最终树8731518（已合并main fdc0a05）；本次发现与PR160的OCR请求身份修复不同。

## 实际行为

上传先扫描并解析原生文字/视觉事实；Chat发送前根据模型vision能力准备正文与页面图，再做上下文预算。PPT以slide.shapes非空标视觉，文本框/表格也计入，因此不是仅照片页。Word按drawing/pict/object及包内media/charts/diagrams判视觉；Excel按工作簿drawings/charts/media存在判视觉，标记偏全局。Word/Excel逻辑锚点不能直接映射渲染物理页，含视觉时走整份分页准备。PDF按每页图像/路径/渐变等绘制算子判视觉；只有无原生文字的page锚点才触发自动OCR路径，不能把现有文字质量当已验证。

页面图片与正文一同组成同一模型请求，不逐页单独调用LLM，不直接传PPT/Word/Excel/PDF原始二进制或逐一拆嵌图。正常分支每文件最多20张视觉页面，正文200000字符且还受整轮预算约束；整文渲染最多前40页，超出完整视觉准入应拒绝。原生预览与模型输入不同。

## P1：部分页缓存可漏标视觉截断（尚未修复）

位置：platform/ailab_platform/api/routes_library_tools.py 的document_images，约435–471行。all_pages路径在ocr_preview未ready时使用meta.render_page_count选前20页，但返回truncated与page_count只读取ocr_preview.page_count，未使用已知render_page_count。

最小函数探针：docx、render_page_count=30、普通preview.pending、ocr_preview缺失，RenderedPage查询返回1至20页；调用document_images(db,file,None,prepare=False)实际返回20页、pending缺省false、truncated=false、page_count=None。探针使用受控DB返回值和真实函数，未访问业务数据库/运行服务、未做端到端并发复现；证明该数据态下覆盖标记错误，不能宣称已观察到真实用户漏页。页面逐张发布时会更新render_page_count，故需补缓存准备过程的并发端到端回归。

风险：Chat的校验和最终输入都依赖这个truncated标记，可能将部分页集合当作完整视觉准备。前述“超限必拒绝、不静默只发前20页”只表达意图，当前实现不能作绝对保证。

修复建议：统一effective_page_count，选页/返回计数/截断/完成状态共用同一事实；完成所需集合全部齐备前不返回ready。回归覆盖Word/Excel已知总页数+部分缓存、最终manifest迟到、超过20页、缓存完整及PDF已知精确视觉页集合。PPT的过宽视觉判定、Word/Excel整份准备开销和1280长边小字可读性属于另需评估的既有边界，不能混为本项已复现缺陷。
