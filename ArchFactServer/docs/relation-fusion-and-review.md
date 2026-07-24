# 区域关系匹配、结果融合与人工审核

## 处理顺序

1. PDF 预处理生成文本块和分页图片。
2. 检测适配器生成 `artifact`、`number`、`caption`、`line_drawing`、`color_plate` 等区域。
3. `RelationMatcher` 对每一类关系建立完整得分矩阵，使用匈牙利算法求全局最优匹配。
4. `ResultFusionService` 将 Coze/本地抽取字段的文本证据与检测区域对齐，并把区域、关系 ID 回填到记录和证据。
5. 前端依据真实 `source_region_id -> target_region_id` 绘制连线，不生成模拟关系。
6. 人工可通过、驳回或重新绑定关系；操作追加写入 `relation_revisions`，不会覆盖审计历史。

## 匹配得分

匹配得分由布局先验、归一化中心距离、水平/垂直重叠和检测置信度共同构成。低于
`RELATION_MATCHING_MIN_SCORE` 或超过 `RELATION_MATCHING_MAX_DISTANCE` 的候选不会强制配对。
各项权重可通过 `.env` 调整，默认值见 `.env.example`。

当前默认关系包括：

- `number -> artifact`：`number_of`
- `caption -> artifact`：`caption_of`
- `line_drawing -> artifact`：`drawing_of`
- `color_plate -> artifact`：`image_of`
- 抽取文本证据与视觉区域融合时：`evidence_for`

## 人工审核接口

| 方法 | 地址 | 用途 |
|---|---|---|
| `PATCH` | `/api/v1/extraction-jobs/{job_id}/relations/{relation_id}/review` | 通过、驳回或恢复未审核状态 |
| `POST` | `/api/v1/extraction-jobs/{job_id}/relations/{relation_id}/rebind` | 保留原关系并创建人工绑定的新关系 |
| `GET` | `/api/v1/extraction-jobs/{job_id}/relations/{relation_id}/revisions` | 查询关系修订历史 |

重新绑定后，原关系标记为 `rejected`，新关系标记为 `accepted`，二者通过
`supersedes_relation_id` / `superseded_by_relation_id` 相互追踪。

## 接入真实 YOLO 前需要确认

- 模型文件（通常为 `.pt` 或导出的 ONNX/TensorRT 文件）及模型版本；
- 完整类别 ID 到区域类型的映射；
- 推理输入尺寸、置信度阈值、IoU/NMS 阈值；
- 运行设备（CPU/CUDA）和部署方式；
- 至少一份代表性 PDF 与期望检测结果，用于端到端验收。

这些信息确定后，只需新增 `DetectionEngine` 实现并在工厂中注册，已有任务接口、关系匹配、
结果融合、数据库结构和前端交互均无需改变。
