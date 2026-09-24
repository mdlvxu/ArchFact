# 更新日志

[English](./CHANGELOG.md) | [中文](./CHANGELOG中文.md)

## quality-baseline-v6 — 2026-09-24

相对 `quality-baseline-v5`（2026-09-20）的主要修改与优化。

### 提取模板与器物卡片范围

- 新增系统公共提示词编辑、字段提示词编辑、模板提示词预览；新增“最新器物卡片提取模板”，并保留基础研究模板
- 纯文本证据记录继续保留在库中，但目录与机器校验仅展示、使用已关联裁剪图且按器物实体去重的卡片
- 实验摘要、全量校验、人工样本和导出统一使用同一套有效器物统计口径；已有实验无需重提 PDF 即可回填正确数量

### 断言实验与导出

- 新增独立实验基线：V1 使用 LLM 断言 V1 和固定 18 条人工样本；V2 使用 LLM 断言 V2 并复用该样本，以便比较指标
- 全量机器校验新增暂停、继续和终止；历史实验仅支持查看与导出
- 新增校验结果 JSON 与逐器物全量机器校验 Excel 明细导出

### 界面、启动脚本与文档

- 完善实验栏、运行进度、实验切换下拉框和导出菜单的中英文切换
- 强化 Windows 启动状态与停止脚本，避免重启后误处理失效 PID
- 重写中英文操作流程，并更新中英文流程截图

### 验证

- 前端：`pnpm build` 通过
- 后端：`tests/test_machine_verification.py` 10 项通过

---

## quality-baseline-v5 — 2026-09-20

相对 `quality-baseline-v4`（2026-09-07）的主要修改与优化。

### PaddleOCR 3.x 与 PP-OCRv6_small

- 默认 OCR 为 PaddleOCR 3.7（`ppocr3`）+ `PP-OCRv6_small`；通过 BOS 下载到 `models/paddleocr`（权重仍不进 git）
- 硬件自适应：small/tiny/v4 最多 8 个 OCR worker；medium/server/v5 上限仍为 2，避免 CPU 内存打满
- Worker 就绪握手，模型加载不计入页面超时（默认 180 秒）；超时后将 `max_side` 从 1600 降到 960 重试
- 页 OCR 缓存按 provider/model/version/text/blocks 命中；超时和 worker 数不再让缓存失效
- 编号区域复用整页 OCR；剩余裁切并行识别，单区域超时 20 秒

### 抽取稳健性与界面

- LLM 截断 JSON 会修复或对半拆分（5054 / 5022），避免整页语义抽取失败
- 任务运行或停止中禁用「开始提取」，并显示进度百分比

### 发布布局与死代码

- 根目录 `.gitignore` 白名单纳入启动脚本和双语更新日志
- 删除未使用的前端模块（`DocumentSheet`、`ExtractionResults`、`api/modules/user`、`stores/app`）
- 抽取路由不再二次导出 application 辅助函数；测试从 application/domain 直接导入
- SETUP_WINDOWS 将 `ppocr3` + `PP-OCRv6_small` 写为推荐 OCR；2.9 仍可作为回退

### 测试

- 前端：`pnpm test:run` 83 通过、1 跳过
- 后端：`pytest` 213 通过
- 更新：OCR 就绪握手、内置模型目录、硬件 worker 上限、JSON 挽救等用例

---

## quality-baseline-v4 — 2026-09-07

相对 `quality-baseline-v3`（2026-08-13）的主要修改与优化。

### 大报告导入与任务续跑

- 默认上传上限 512 MB；Vite 代理和前端超时随文件大小拉长
- 页面导航展示上传 / 保存 / 解析进度，避免 `arrayBuffer()` 卡住界面
- 上传不再手写无 boundary 的 `Content-Type`；PDF.js 的 blob URL 在文档释放前保持有效
- FastAPI 启动时恢复中断的抽取任务，并跳过已经完成的页面
- 进程重启后，将僵死的重匹配、AI 复核和质量评估标记为失败

### 硬件自适应与 MongoDB

- 默认 `HARDWARE_AUTO_TUNE=true`，按本机 CPU/内存/GPU 设置 OCR 进程数、发现并发和分页批大小
- `documents.sha256` 唯一，相同 PDF 不再二次写入 GridFS
- 补充复合索引；`job_events` 60 天 TTL，语义缓存 90 天 TTL
- 每个任务仅允许一个进行中的核验会话（部分唯一索引）

### 刷新后接上正在跑的任务

- `GET /extraction-jobs/recent/latest?include_active=true` 优先返回排队 / 抽取中的任务
- 刷新数据提取页时，不再被 `localStorage` 里上一本已完成报告盖住当前任务
- 页面导航列表占满剩余高度，最后一页缩略图和页码不再被裁切

### 后端结构

- 领域辅助放到 `app/domain/`，视图与字段补全放到 `app/application/`
- 抽取路由按 jobs / records / rematches / verification 拆分，URL 前缀仍是 `/extraction-jobs`
- `MongoRepository` 改为 mixin 门面；`result_fusion` 仍为单文件，版本升至 v24

### 测试

- 前端：`pnpm test:run` 83 通过、1 跳过
- 后端：`pytest` 190 通过
- 新增：PDF 导入进度、硬件自适应、Mongo 索引、机器核验等用例

---

## quality-baseline-v3 — 2026-08-13

相对 `quality-baseline-v2`（2026-08-12）的主要修改与优化。

### 抽取前的彩图页角色

- 页面发现 v2 按置信度划分页面类型（`color_plate`、`color_visual`、`mixed_visual`、`monochrome_visual`、`document`、`blank`）
- 整本任务会先建立页面索引
- 连续彩图页保留 OCR 与 YOLO 用于关联，但不进入 LLM 语义抽取和正文 OCR 逻辑索引

### 仅有图版的卡片找回正文主人

- 稀疏彩图注记卡（如 `T03022:3`）在正文 OCR 唯一命中时恢复文本来源，并把 `source_pages` 从彩图页移走
- 图号分图裁切在标签 OCR 乱码时仍能绑定（如 `3.102022:34` → `T03022:34`）
- 修复圈号单位两侧的 OCR 括号（`T0302(②：34` → `T03022:34`）

### 段落字段不再串到下一条器物

- 正文换行补全在遇到下一个不同器物号时停止
- 尺寸、图注、形态只取本条编号范围内的 OCR（去掉上一条尾巴和后续标本）
- 重匹配时覆盖已经写脏、吞入 `T02037` 等后续编号的字段
- 不把「标本」这类目录前缀当成器类

### 测试

- 新增/扩展：仅彩图卡恢复正文、乱码图号绑定、重匹配字段污染、目录前缀不当器类等融合回归

---

## quality-baseline-v2 — 2026-08-12

相对 `quality-baseline-v1`（2026-07-28）的主要修改与优化。

### 作业耗时

- 任务完成时持久化 `completed_at`，前端优先用其计算「已用时间」
- 避免 rematch/apply 刷新 `updated_at` 后把耗时算成上百小时
- 启动时将僵死抽取任务标记为失败，并冻结完成时间

### 器物卡片与文本证据

- 段落补全升级（融合 v15→v16）：从 OCR 段落回填/升级类别、质地、形态描述
- 短形态描述（如「片状」）在 OCR 更完整时自动升级
- 测量字段避免吞入下一条器物编号；单位「厘米」跨行时正确拼接
- 列表/证据接口可持久化段落补全结果（`paragraph_enrichment_version`）
- 前端目录卡片优先展示形态描述，并可用 `text_evidence` 兜底

### 预览布局（彩图不占第一列）

- 主文本页选择时排除彩图页（`page_type=color_plate` 或彩图 region）
- 左侧预览列始终优先非彩图正文；彩图仅作为可选第三列关联
- 前端增加 `preview-document-page` 安全兜底

### 彩图注记 ≠ 目录正文（如 M4:3 / 仲M4:3）

- **不以彩图页 OCR 作为文本证据**；彩图仅作关联
- 吸收/丢弃空彩图注记卡（如 `4.玉锥形饰（仲M4：3）`），并入正文器物卡的关联信息
- 编号归一化：剥离墓/单位前缀（`仲M4:3` → `M4:3`），实体链接一并处理
- 目录侧过滤仅含编号/图注的彩图空卡，避免搜索出现多张空卡片

### 测试

- 新增/扩展：融合吸收彩图注记、实体前缀合并、证据上下文主文本页、目录空卡过滤等用例

---

## quality-baseline-v1 — 2026-07-28

首个质量基线：内容预览、PDF 导航、器物卡片联动、文本证据提取、彩图/图注关联与核验界面。详见各子项目 `BASELINE.md`。
