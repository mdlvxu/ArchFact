# OCR + DeepSeek + YOLO 结构化抽取设计

## 目标

前端只创建“抽取任务”。后端负责完成以下流水线，前端不直接调用 OCR、DeepSeek 或 YOLO：

```text
PDF -> 选定页渲染 -> OCR -> 语义分块 -> DeepSeek 结构化 -> MongoDB records
                 -> YOLO 检测/裁剪 -> MongoDB source_regions
records + source_regions -> 标识符/图号/页码窗口匹配 -> region_relations
records + region_relations -> 文档级器物实体归并 -> artifact_entities
器物卡片 -> evidence-context API -> PDF 页、标注框、裁剪图与蓝色关系线
```

## 数据职责

- `pages`：同时保存 PDF 文字层和 OCR 结果，字段包括 `pdf_text`、`ocr_text`、两套 blocks、OCR 状态和当前有效文本来源。
- `records`：保存大模型输出的动态字段、逐字证据、`link_hints`、关联页、区域 ID 和关系 ID。
- `source_regions`：保存 YOLO/OCR 得到的器物、线图、编号、图注、彩版等区域及裁剪图相对路径。
- `region_relations`：保存器物、编号、图注、文本证据之间的可审核关系。
- `artifact_entities`：保存跨页器物实体，聚合同一器物的正文记录、线图、裁剪图和彩版，并记录匹配键、置信度与关联页。
- 本地文件系统：保存分页 PNG 和 YOLO 裁剪图；MongoDB 只保存相对 `object_key`。

## 大模型输入输出边界

DeepSeek 接收当前 OCR 页文本、OCR blocks 和前端规则快照。输出必须是 JSON，且只能包含规则中声明的字段。每条记录还需返回：

- `artifact_ids`：器物号，例如 `H125:1`；
- `figure_refs`：图号或插图引用；
- `plate_refs`：彩版引用；
- `aliases`：可用于匹配的同义编号；
- 每个字段的 `evidence.quote`：必须逐字来自 OCR 原文。

Python 负责校验、后处理、证据坐标补全和持久化，不信任模型直接给出的坐标或数据库 ID。

## 语义分块

- 大模型请求不会机械地按固定字符截断；后端优先识别器物编号、图号和彩版号作为语义边界。
- 同一器物编号后的形态、尺寸、质地和引用说明会尽量保留在同一个文本块中。
- 只有单个语义段本身超过模型输入上限时，才按 OCR block 拆分并保留少量上下文重叠。
- 分块只解决单次请求大小与并发吞吐，不限制跨页关联范围；跨页关系在全部页级结果完成后统一计算。

## 关系匹配规则

1. 优先使用器物号、图号等显式标识符进行文本匹配。
2. 默认候选页窗口为文本证据页前后 3 页。
3. 对完全一致的明确标识符允许跨越页窗口，例如正文第 19 页关联线图第 62 页。
4. 通过已存在的 `caption_of`、`number_of`、`drawing_of`、`contains` 等关系扩展到器物区域。
5. 匹配结果保存为关系，而不是只在前端临时计算，便于人工接受、拒绝或重新绑定。
6. 后端再使用器物编号、`图号+子图序号`、`彩版号+子图序号`建立稳定实体键，把非相邻页记录归并到同一 `artifact_entity`。
7. 仅有“图6”或“彩版8”这类不含子图序号的弱引用，不允许直接做远距离自动合并，避免一对多误关联。

当前任务会先保留用户选择页，再通过全文轻量索引自动扩展明确关联的候选页。因此器物
正文、线图和彩版只要存在可识别的器物号、图号或彩版号，就可以跨任意页距进入同一次
关系融合；自动扩展页不会进入用户选择页导航，也不会额外调用 DeepSeek。

## 整本 PDF 轻量发现索引

当前版本已经增加 `page_discovery` 阶段，页码没有固定范围，“第 100 页”只代表可能与
正文相隔很远的任意页面：

1. 使用 PyMuPDF 一次遍历整本 PDF，读取文字层、图片数量和矢量绘图数量。
2. 按 `DISCOVERY_THUMBNAIL_SCALE` 生成内存中的低分辨率页面，只计算颜色、墨迹和边缘特征，不保存高清分页图。
3. 将页面分类为正文、黑白线图候选、彩版候选、混合图文或空白页。
4. 从文字层规范化提取器物编号、`图号+子图号`、`彩版号+子图号`。
5. 用户选择页完成正常 OCR 后，如果引用仍未命中，只对视觉候选页运行低分辨率 Tesseract OCR；找到目标引用即可提前停止。
6. 命中的页面写入任务 `discovered_pages`，随后运行高清渲染、完整 OCR、YOLO 和区域 OCR，但设置 `discovery_only=true`，不会调用 DeepSeek。
7. 页面索引保存在 MongoDB `document_page_index`；同一个 PDF 后续修改模板或重新抽取时直接复用。

索引匹配不使用页码距离作为过滤条件。前后 3 页仍只用于缺少明确编号时的局部空间关系
辅助；明确图号和彩版号可以召回 PDF 中任意位置的页面。

相关配置：

```dotenv
DISCOVERY_ENABLED=true
DISCOVERY_THUMBNAIL_SCALE=0.30
DISCOVERY_OCR_RENDER_SCALE=0.75
DISCOVERY_OCR_CONCURRENCY=2
DISCOVERY_OCR_MAX_PAGES=80
DISCOVERY_MAX_RECALLED_PAGES=24
```

`DISCOVERY_OCR_MAX_PAGES=0` 表示不限制低分辨率候选 OCR 页数。几百页报告建议先使用 80，
根据实际召回率和耗时调整；候选排序依据页面视觉类型和引用匹配强度，不依据与正文的距离。

## 第二页行为

- 浏览模式：右侧显示所有结构化器物卡片，不显示 PASS/FAIL 和“完成查看”。
- 点击卡片：调用 `GET /api/v1/extraction-jobs/{job_id}/records/{record_id}/evidence-context`。
- 中间主画布显示主要文本证据页的完整 PDF；跨页线图或彩版以完整页面卡片显示，并叠加真实区域框。
- 下方“关联页面”只显示 evidence-context 返回的真实证据，不再用前后相邻页占位。
- 核验模式：沿用同一证据上下文，但只显示核验会话中的样本，并开放 PASS/FAIL。

## DeepSeek 启用配置

开发时可先用 `EXTRACTION_ENGINE=local` 验证 OCR、YOLO、数据库和前端链路。准备好 API Key 后修改 `.env`：

```dotenv
EXTRACTION_ENGINE=llm
LLM_PROVIDER=deepseek
LLM_API_BASE=https://api.deepseek.com
LLM_API_KEY=你的_API_Key
LLM_MODEL=deepseek-v4-flash
LLM_TIMEOUT_SECONDS=120
LLM_MAX_RETRIES=2
LLM_MAX_CONCURRENCY=3
LLM_MAX_TOKENS=8192
LLM_THINKING=false
OCR_POLICY=all
```

`LLM_MAX_CONCURRENCY` 控制同一服务实例同时发出的结构化抽取请求数。建议从 2～3 开始，
结合 DeepSeek 账户的 RPM/TPM 限额逐步调整；不要直接设置成页面数，避免触发限流。
后端会复用同一个 HTTP 连接池，并在任务的 `page_metrics` 中记录每页 YOLO、区域处理、
语义抽取和总耗时。

修改后重启 FastAPI。密钥只保存在后端 `.env`，不得提交到版本库或返回给前端。

## 立即停止任务

- 前端点击“停止任务”后立即结束轮询并恢复上传、页码选择和重新抽取操作。
- `POST /api/v1/extraction-jobs/{job_id}/cancel` 会直接取消本机任务协程，而不再只设置 `cancel_requested` 等待下一页。
- 正在等待的大模型 HTTP 请求和分块并发请求会随父任务一起取消。
- Tesseract 使用可管理的异步子进程；取消时会直接终止当前 OCR 进程。
- 所有正在运行的 `model_runs` 会改为 `cancelled`，任务状态最终固定为 `cancelled`，不再继续关系匹配、融合或保存最终结果。
- PyMuPDF 渲染和 Ultralytics YOLO 属于原生库的单次同步调用，Python 无法安全强杀正在执行的线程；取消后其结果会被丢弃且不会进入后续阶段。若生产环境要求连 GPU 内核也强制立即释放，应把 YOLO 放入独立 Worker 进程，再通过进程终止或队列撤销实现硬取消。
