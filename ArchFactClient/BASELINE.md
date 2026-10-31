# ArchFactClient 代码基线

- 基线日期：2026-10-31
- 基线分支：`main`
- 基线标签：`quality-baseline-v7`
- 配套后端：`ArchFactServer` 的同名标签
- 上一基线：`quality-baseline-v6`（2026-09-24）

## 固定范围

该基线包含数据提取、数据预览、机器校验、GT 评测和数据洞察五个页面。
数据预览展示融合后的器物卡片、原文证据与图文关联；机器校验保留独立的
V1/V2 断言实验与 18 条人工样本；GT 评测按报告复用人工标注基准，支持
不同提取任务及关系版本的逐条对照、日志和导出。中英文界面与操作说明同步更新。

相对 v6 的变化见仓库根目录 [CHANGELOG中文.md](../CHANGELOG中文.md) /
[CHANGELOG.md](../CHANGELOG.md)。移动端实验工程、依赖、构建结果、
本机环境变量、运行日志、导出数据和工具密钥不属于本代码基线。

## 验证结果

在基线提交前执行：

- `pnpm lint:check`、`pnpm type-check`、`pnpm build`：通过。
- `pnpm test:run`：126 项通过，1 项跳过。

## 回退方式

先保存未提交的本地工作，再切换到 `quality-baseline-v7`、
`quality-baseline-v6` 或更早的标签查看对应基线。
前后端请使用同名标签配套回退。
