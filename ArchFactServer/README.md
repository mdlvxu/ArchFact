# ArchFact PDF Extraction Server

YOLO 区域裁剪、局部 OCR 与整体分组说明见
[docs/region-crops-and-group-matching.md](docs/region-crops-and-group-matching.md)。

区域全局匹配、结果融合及人工关系审核说明见
[docs/relation-fusion-and-review.md](docs/relation-fusion-and-review.md)。

这个目录实现了可独立交付的 PDF 抽取模块：PDF 上传、分页文本解析、异步抽取任务、
MongoDB 持久化，以及可替换的本地/Coze/后续 YOLO 模型流水线。

原始 PDF 保存在 MongoDB GridFS；分页渲染图保存在 `FILE_STORAGE_ROOT` 指定的本地目录，
MongoDB 只保存相对 `object_key`、页码、尺寸和哈希，不保存操作系统绝对路径。

## 模块边界

```text
Vue -> /documents -> GridFS + documents
Vue -> /extraction-jobs -> LocalJobDispatcher -> ExtractionPipeline
                                             -> PdfParser
                                             -> Source Regions + Region Crops/OCR
                                             -> ExtractionEngine
                                                  |- local
                                                  |- coze
                                                  `- future adapters (OCR/YOLO/...)
                                             -> Relations + PostProcessor
                                             -> Records + Model Runs
```

前端永远调用 `/extraction-jobs`，只提交稳定的 `pipeline_id`，不会接触 Coze Token、
Workflow ID、模型文件路径或任一平台的原始返回值。

`source_regions` 统一保存文本、线图、彩版和器物检测框；`region_relations` 保存区域之间的
配对关系；`model_runs` 记录每个阶段实际使用的提供方、模型和版本；`record_revisions`
以追加方式保存字段级人工修订，原始 `raw_value` 不会被覆盖。

## 本地启动

1. 启动 MongoDB。可以使用本目录的 `docker-compose.yml`，也可以使用现有 MongoDB。
2. 从 `.env.example` 复制一份 `.env`。
3. 安装并启动：

```powershell
cd D:\work\ArchFact\ArchFactServer
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe run.py
```

接口文档地址为 `http://localhost:8080/docs`。前端 Vite 已将 `/api` 代理到该端口。

## 两种抽取引擎

开发联调：

```dotenv
EXTRACTION_ENGINE=local
```

`local` 模式不伪造考古字段，只返回每页 `page_text`，用于验证上传、解析、进度、结果展示
的完整链路。

Coze 模式：

```dotenv
EXTRACTION_ENGINE=coze
COZE_API_TOKEN=pat_xxx
COZE_WORKFLOW_ID=xxxxxxxx
```

工作流的准确输入、输出和节点配置见 [Coze 工作流设计](docs/coze-workflow.md)。

## 页面预处理和 YOLO 联调适配器

任务启动后会批量解析并渲染用户选中的页面。分页 PNG 保存到
`documents/{document_id}/pages/{page_no}/rendered/page.png`，MongoDB 只保存相对路径。
PaddleOCR 以常驻工作进程池运行；每个 OCR 批次完成后，对应页面会立即进入语义抽取，
不需要等待整本文档完成 OCR。相同文档、渲染参数和 OCR 配置再次执行时会复用分页图、
OCR 文本与坐标缓存。每页阶段状态和耗时保存在 `job_page_runs`，用于断点诊断和恢复基础。

整本文档性能相关配置：

```dotenv
SEMANTIC_PAGE_CONCURRENCY=3
PADDLE_OCR_WORKERS=2
PADDLE_OCR_WORKER_THREADS=6
```

当请求已经覆盖 PDF 全部页码时，后端会跳过只服务于“部分页跨页召回”的重复发现阶段；
YOLO、OCR、结构化抽取和最终全局关系匹配仍会覆盖全部页面。

真实 YOLO 尚未接入时，可以使用进程内 JSON 适配器验证区域数据链路：

```dotenv
YOLO_ADAPTER=json
YOLO_PREDICTIONS_PATH=D:/work/ArchFact/predictions.json
```

预测文件使用归一化坐标：

```json
{
  "pages": {
    "3": [
      {"class_id": 0, "bbox": [0.1, 0.2, 0.4, 0.6], "confidence": 0.92}
    ]
  }
}
```

类别约定为 `0=artifact`、`1=number`、`2=caption`、`3=other`。以后接入训练好的
YOLO 时只需替换 `DetectionEngine` 实现，任务接口和前端不需要变化。

## 当前接口

| 方法 | 地址 | 说明 |
|---|---|---|
| `POST` | `/api/v1/documents` | 上传 PDF 到 GridFS |
| `GET` | `/api/v1/documents/{document_id}` | 查询 PDF 状态 |
| `POST` | `/api/v1/documents/{document_id}/pages/{page_no}/image` | 按需生成分页图片 |
| `GET` | `/api/v1/documents/{document_id}/images` | 查询文档图片元数据 |
| `GET` | `/api/v1/documents/{document_id}/images/{image_id}/content` | 读取图片内容 |
| `GET/PUT` | `/api/v1/extraction-templates` | 查询或保存抽取模板 |
| `GET/PUT` | `/api/v1/post-processing-rules` | 查询或保存后处理规则 |
| `POST` | `/api/v1/extraction-jobs` | 创建异步任务 |
| `GET` | `/api/v1/extraction-jobs/{job_id}` | 获取进度和日志 |
| `POST` | `/api/v1/extraction-jobs/{job_id}/cancel` | 请求取消任务 |
| `GET` | `/api/v1/extraction-jobs/{job_id}/records` | 分页读取结果 |
| `PATCH` | `/api/v1/extraction-jobs/{job_id}/records/{record_id}/review` | 保存人工 PASS/FAIL 审核状态 |
| `GET` | `/api/v1/extraction-jobs/{job_id}/pages/{page_no}/annotations` | 读取真实区域、关系和本页记录 |
| `GET` | `/api/v1/extraction-jobs/{job_id}/model-runs` | 查询阶段与模型运行记录 |
| `PATCH` | `/api/v1/extraction-jobs/{job_id}/records/{record_id}/fields/{field_key}/review` | 字段级确认、驳回或修订 |
| `GET` | `/api/v1/extraction-jobs/{job_id}/records/{record_id}/revisions` | 查询字段修订历史 |
| `POST` | `/api/v1/gold-datasets/import/wenjiashan` | 把文家山人工标注绑定为指定 PDF 的独立评测金标准 |
| `GET` | `/api/v1/extraction-jobs/{job_id}/ai-verification-runs/{run_id}` | 查询异步 AI 复核进度与冲突数 |

抽取证据中的 `bbox` 统一使用 `[left, top, right, bottom]`，四个值均为 0–1 的 PDF 页面
相对坐标。证据可通过 `region_id` 和 `relation_ids` 追溯到检测区域及其配对关系。Coze
只需返回原文 `quote`；Python 会根据解析后的 PDF 文本块补全坐标和区域 ID。

## 金标准与 AI 复核

金标准存放在独立的 `gold_*` 集合中，不参与 OCR、结构化抽取、YOLO 检测或关系匹配，
只在 18 条固定样本完成人工 PASS/FAIL 后用于质量评测。图片继续保存在
`GOLD_DATASET_ROOT`，MongoDB 的 `gold_assets.object_key` 只保存相对路径。

首次使用时，把已上传的文家山 PDF 与金标准显式绑定：

```http
POST /api/v1/gold-datasets/import/wenjiashan
Content-Type: application/json

{"document_id":"doc_xxx","version":"1.0","replace":false}
```

完成18条人工核验后，`complete` 接口会立即返回一个后台 AI 复核任务。前端轮询任务：
人机一致则自动生成 V 版本；人机冲突或 AI 无法判断时返回第二页做最终人工确认，确认后
再次调用 `complete` 才冻结版本。大模型只收到自动结果、OCR证据和金标准，不会收到人工
PASS/FAIL，且复核结果不会反写生产抽取记录。

## 生产化边界

- 当前 `LocalJobDispatcher` 是开发适配器，应用进程重启时未完成任务不会自动恢复。
- 生产环境应新增实现相同边界的 Celery/Redis dispatcher，并在启动时恢复 `queued` 任务。
- 没有文字层且 OCR 失败的扫描页会明确标记 `needs_ocr` 并产生任务警告，不会伪造结果。
- OCR、YOLO 和语义抽取均保留独立模型运行记录、页面检查点与证据契约。
- 单页失败不会终止其他页面；存在可用结果时任务以 `completed_with_warnings` 结束。
