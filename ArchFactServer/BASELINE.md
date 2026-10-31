# ArchFactServer 代码基线

- 基线日期：2026-10-31
- 基线分支：`main`
- 基线标签：`quality-baseline-v7`
- 配套前端：`ArchFactClient` 的同名标签
- 上一基线：`quality-baseline-v6`（2026-09-24）

## 固定范围

该基线包含 PDF 存储复用、OCR/YOLO/LLM 提取、器物编号及图文关系匹配、
融合记录与证据存储、机器校验，以及按人工标注 Excel 进行的 GT 评测。
GT 基准按报告保存，可对同一报告的不同提取任务和关系版本分别评测；
评测结果、逐条对照与日志分别保留，并支持明细导出。

相对 v6 的变化见仓库根目录 [CHANGELOG中文.md](../CHANGELOG中文.md) /
[CHANGELOG.md](../CHANGELOG.md)。本机 `.env`、MongoDB/GridFS 运行数据、
OCR/YOLO 模型权重、虚拟环境、缓存和日志不属于代码基线。

## 验证结果

在基线提交前执行：

- `python -m ruff check .`：通过。
- `python -m pytest tests -q`：265 项通过。

## 运行依赖

运行时从 `.env.example` 配置本机 `.env`，并按需准备 MongoDB、
OCR 服务和 `models/archaeology-yolo/v1/best.pt`。
模型与本机服务配置不随标签提交。

## 回退方式

先保存未提交的本地工作，再切换到 `quality-baseline-v7`、
`quality-baseline-v6` 或更早的标签查看对应基线。
前后端请使用同名标签配套回退。
