# ArchFactServer 代码基线

- 基线日期：2026-08-12
- 基线分支：`main`
- 基线标签：`quality-baseline-v2`
- 配套前端：`ArchFactClient` 的同名标签
- 上一基线：`quality-baseline-v1`（2026-07-28）

## 固定范围

该基线固定当前 PDF 解析、OCR/YOLO 区域处理、器物关系匹配、文本证据段落补全、
彩图与图注**仅关联**、人工核验及质量评估能力。融合服务版本为 `result_fusion` v16。

相对 v1 的主要优化见仓库根目录 [CHANGELOG.md](../CHANGELOG.md)。

本地环境变量、MongoDB 与文件运行数据、日志、Python 虚拟环境、缓存、
Ultralytics 本机配置和模型权重不属于代码基线。

## 验证结果

在基线提交前执行：

- `pytest tests/test_result_fusion.py tests/test_artifact_entity_linker.py tests/test_evidence_context.py -q`：通过。
- 完整 `pytest -q` / `ruff check app tests`：以本地开发机最新结果为准。

## 运行依赖

运行时需要根据 `.env.example` 创建本地 `.env`，并按需准备 MongoDB、OCR 服务和
`models/archaeology-yolo/v1/best.pt`。这些机器相关内容被明确排除，不会随基线提交。

## 回退方式

先保存未提交的本地工作，再切换到 `quality-baseline-v2` 或更早的
`quality-baseline-v1` 标签即可查看对应基线。
前后端请使用同名标签配套回退。
