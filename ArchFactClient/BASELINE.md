# ArchFactClient 代码基线

- 基线日期：2026-09-07
- 基线分支：`main`
- 基线标签：`quality-baseline-v4`
- 配套后端：`ArchFactServer` 的同名标签
- 上一基线：`quality-baseline-v3`（2026-08-13）

## 固定范围

该基线固定大 PDF 导入进度、页面导航完整滚动、刷新后接上正在抽取的任务，
以及内容预览、器物目录卡片、彩图第三列关联和机器核验工作台。

相对 v3 的主要优化见仓库根目录 [CHANGELOG中文.md](../CHANGELOG中文.md) / [CHANGELOG.md](../CHANGELOG.md)。

本地环境变量、依赖、构建结果、运行日志、工具密钥和增量编译缓存不属于代码基线。

## 验证结果

在基线提交前执行：

- `pnpm test:run`：83 通过，1 跳过。
- `vue-tsc --noEmit` / `eslint`：以本地开发机最新结果为准。

## 回退方式

先保存未提交的本地工作，再切换到 `quality-baseline-v4` 或更早的
`quality-baseline-v3` / `quality-baseline-v2` / `quality-baseline-v1` 标签即可查看对应基线。
前后端请使用同名标签配套回退。
