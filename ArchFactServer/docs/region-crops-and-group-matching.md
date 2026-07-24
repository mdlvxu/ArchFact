# 区域裁剪、局部 OCR 与整体分组

当前 YOLO 类别按以下业务语义处理：

| YOLO 类别 | 区域类型 | 处理方式 |
|---|---|---|
| `qiwu` | `artifact` | 保存器物裁剪图 |
| `xuhao` | `number` | 保存裁剪图，并以单行模式执行局部 OCR |
| `tuzhu` | `caption` | 保存裁剪图，并以文本块模式执行局部 OCR |
| `muzang` | `grave_drawing` | 保存墓葬图裁剪图 |
| `zhengti` | `group` | 只作为关系分组边界，不保存冗余裁剪图 |

局部 OCR 会先对小裁剪图执行灰度、自动对比度、锐化和按尺寸放大。低于
`REGION_OCR_MIN_CONFIDENCE` 的候选不会写入可参与融合的 `text`，但会保存在
`ocr_raw_text` 中供审计和后续人工复核。

裁剪图片存放在本地文件系统，MongoDB 的 `source_regions.crop_object_key` 只保存相对路径。
前端通过以下稳定接口读取图片：

```http
GET /api/v1/extraction-jobs/{job_id}/regions/{region_id}/crop
```

关系匹配先根据 `group` 对内部区域分桶，再在各组内匹配序号、图注和器物，避免不同器物卡片
之间串联。组级图注会同时关联整体框和组内器物。未检测到 `group` 时自动退回页面级全局匹配，
因此旧模型与旧数据仍可继续使用。

前端不会把 `group` 画成可点击证据框；`artifact` 与 `grave_drawing` 作为视觉证据展示，证据卡片
优先使用区域裁剪图，裁剪图缺失时才退回完整页缩略图。
