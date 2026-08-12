# ArchFactClient 代码基线

- 基线日期：2026-08-12
- 基线分支：`main`
- 基线标签：`quality-baseline-v2`
- 配套后端：`ArchFactServer` 的同名标签
- 上一基线：`quality-baseline-v1`（2026-07-28）

## 固定范围

该基线固定当前内容预览、PDF 长图导航、器物目录卡片、彩图第三列关联、
文本证据段落补全展示，以及目录侧对彩图注记空卡的过滤。

相对 v1 的主要优化见仓库根目录 [CHANGELOG.md](../CHANGELOG.md)。

本地环境变量、依赖、构建结果、运行日志、工具密钥和增量编译缓存不属于代码基线。

## 验证结果

在基线提交前执行：

- `pnpm test:run`：相关目录与预览用例通过（含 `catalog-records`、`preview-document-page`）。
- `vue-tsc --noEmit` / `eslint`：以本地开发机最新结果为准。

## 回退方式

先保存未提交的本地工作，再切换到 `quality-baseline-v2` 或更早的
`quality-baseline-v1` 标签即可查看对应基线。
前后端请使用同名标签配套回退。
