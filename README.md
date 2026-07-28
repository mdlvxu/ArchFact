# ArchFact

ArchFact 是一个面向考古报告 PDF 的信息提取与人工核验平台。项目由 Vue 3 前端和 FastAPI 后端组成，使用 MongoDB/GridFS 保存业务数据和原始 PDF，并可选接入 PaddleOCR、YOLO 与大语言模型。

## 项目结构

```text
ArchFact/
├─ ArchFactClient/       # Vue 3 + TypeScript + Vite
├─ ArchFactServer/       # FastAPI + PyMongo + PyMuPDF
└─ SETUP_WINDOWS.md      # Windows 安装、配置与启动指南
```

## 默认端口

- 前端：http://localhost:5173
- 后端：http://localhost:8080
- API 文档：http://localhost:8080/docs
- MongoDB：mongodb://localhost:27017

## 快速开始

请按照 [SETUP_WINDOWS.md](SETUP_WINDOWS.md) 安装 Node.js、pnpm、Python 和 MongoDB，并创建本地 `.env`。

完成配置后按以下顺序启动：

```powershell
# 1. MongoDB（也可使用本机 MongoDB）
Set-Location ArchFactServer
docker compose up -d

# 2. 后端
.\.venv\Scripts\python.exe .\run.py

# 3. 新终端中启动前端
Set-Location ..\ArchFactClient
pnpm dev
```

健康检查：

```powershell
Invoke-RestMethod http://localhost:8080/api/v1/health
Invoke-RestMethod http://localhost:5173/api/v1/health
```

## 本地文件与敏感配置

以下内容不会上传到 GitHub，需要开发者自行配置或备份：

- `ArchFactServer/.env` 中的 DeepSeek、Coze 等 API 密钥
- MongoDB 数据及 `ArchFactServer/.runtime/`
- PaddleOCR 模型缓存和独立 Python 环境
- YOLO 权重 `ArchFactServer/models/archaeology-yolo/v1/best.pt`
- 人工标注与参考资料
- `ArchFactClient/.cursor/mcp.json` 等本机密钥配置

不要将真实密钥、数据库、上传文件或模型权重提交到公开仓库。

## 验证

```powershell
# 前端
Set-Location ArchFactClient
pnpm type-check
pnpm lint:check
pnpm test:run
pnpm build

# 后端
Set-Location ..\ArchFactServer
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

#操作流程说明

系统默认入口：`http://localhost:5173/`  
顶部三个主页面：数据提取 → 数据预览 → 机器校验。

---

## 一、整体流程概览
<!-- 这是一张图片，ocr 内容为：ARCHFACT项目开发与业务处理流程 从PDF输入到结构化数据,支撑知识图谱与专业研究的全流程闭环 04 双通道智能提取 01 03 02 05 90 08 09 07 OCR大模型 结构化字段抽取 V1 V2 上传PDF 创建抽取任务 PDF预处理 融合与存储 数据预览 关系匹配 固定样本核验 版本导出 PDF图像YOLO 区域,关系,记录 文字层,分页渲染 选择模板,页码与 文本序号线图 异步执行与 属性卡片, 18条样本 生成V1/V2 器物,序号,图注裁剪 实时进度 抽取范围 与图片 并导出 检测区域 证据链与人工清洗 与OCR PASS/FAIL -->
![](ArchFactClient/docs/readme-images/archfact-overview.png)

```latex
上传 PDF
  → 配置抽取页码 / 模板并开始抽取
  → 数据预览：核对器物、证据与关联
  → 机器校验：执行校验，进入固定 18 条人工核验
  → AI（DeepSeek）复核
  → 生成版本（如 V1）并回到机器校验页查看结果 / 导出
  → 创建抽取任务
  → PDF 预处理（文字层 / OCR）
  → 双通道智能提取（OCR→大模型 + 图像→YOLO）
  → 关系匹配
  → 融合与存储
  → 数据预览
  → 固定样本核验（18 条 PASS/FAIL）
  → 版本导出（V1 / V2…）
```

---

## 二、启动项目（简要）
1. 启动 MongoDB（本机可用项目内置 `mongod`，端口 `27017`）。
2. 启动后端：进入 `ArchFactServer`，执行 `.\.venv\Scripts\python.exe run.py`（默认 `http://localhost:8080`）。
3. 启动前端：进入 `ArchFactClient`，执行 `pnpm dev`（默认 `http://localhost:5173`）。
4. 浏览器打开前端地址；右上角可切换 **中 / EN**。

---

## 三、页面一：数据提取
**目的：** 上传考古报告 PDF，配置抽取范围，启动后端流水线（分页渲染、OCR、YOLO 检测、语义抽取、关系匹配等）。

<!-- 这是一张图片，ocr 内容为：LOCALHOST:5173 已暂停 品 所有书签 大模型 资源 快捷登录 ARCHFACT 尘导入PDF 数据预览 机器校验 中中EN 数据提取 提取配置 开始提取 处理进度 页面导航 良活遗址群考古报告... 提取模板 INFQ_止任处理理男189贝 面清空日志 已用时间 开始时间 INFO 第189页耗时:YOLO0.135,区域处理0.00S,语义抽取94.645, 基础研究模板 管理 13分4秒 2026-07-27 文家山 SUCCESS第189页处理完成 业导出日志 15:19:27 器物编号 质地尺寸形态描述 表面颜色 INFO正在处理罚190页 重试1个失败页 INFO 第190页每时:YOLO0.09S,区域处理0.005,语义推取0.01S,合 预计剩余 处理速度 类别注完整度 0秒 4.2页/分钟 SUCCESS第190页处理完成 @暂无活动任务 SUSARAA RIPMANLA 添加模板 1/275 良洛遗址拜考古报告之五:文家山(浙江省文物考古研究所)(Z-LIBRARY)(1... 内容预览 100% 第160页:100% 字段约束 器物编号 表面颜色 文本> 文本> 尺寸 质地 文本> 文本> 形态描述 类别 文本 文本> 2/275 图注 完整度 文本> 文本> 文家山 后处理规则 中文数字转阿拉伯数字 回 自动将中文数字转换为阿拉伯数字. 示例-百TO'100* 3/275 单位标准化 -->
![中文图片](ArchFactClient/docs/readme-images/data-extraction-zh.png)

<!-- 这是一张图片，ocr 内容为：LOCALHOST:5173 已暂停 品一 所有书签 大模型 快捷登录 厂资源 ARCHFACT INPUT PDF DATA EXTRACTION DATA PREVIEW MACHINE VERIFICATION 中EN PROCESSING PROGRESS PROMPT START EXTRACTION PAGE NAVIGATOR 良渚遗址群步古报告 EXTRACTION TEMPLATE @ CLEAR LOG ELAPSED TIME START TIME INFO第109页样时:YOLO0.13S,区域处理0.00S,语义抽取94.64S, BASLC RESEARCH TEMPLATE MANAGE 2026-07-27 13M 4S SUCCESS第189页处理完成 业EXPORT LOG 文家山 SURFACE COLOR TEXTURE ARTIFACT ID 15:19:27 INFO正在处理第190页 C RETRY 1 FAILED PAGES MORPHOLOGICAL DESCRIPTION MEASUREMENTS INFO 第190页耗时:YOLOO.09S,区域处理0.00S,语义抽取0.01S,合 PROCESSING RATE TIME LEFT 4.2 PAGES/MIN OS SUCCESS第190页处理完成 FIGURE CAPTION CATEGORY COMPLETENESS NO ACTIVE TASK + ADD TEMPLATE 1/275 良消遗址养考古报告之五:文家山(浙江省文物考古研究所)(Z-LIDRARY)(1... CONTENT PREVIEW 100% PAGE 160-100% PNSTRAINTS 良活遗址群老古报告之五:文家山(浙江省文物老吉研究所)Z-LIBRARY)(1).PDF ARTIFACTID TEXT > TEXT > SURFACE C... TEXT > TEXTURE TEXT > MEASURE... MORPHOLO... TEXT > TEXT CATEGORY 2/275 TEXT > FLGURE CA... TEXT COMPLETE... POST-PROCESSING RULES CHINESE TO ARABIC NUMBER CONVERSION AUTOMATICALLY CONVERT CHINESE NUMBERS TO 回 ARABIC NUMERALS. 3/275 EXAMPLE:-百'TO 100 -->
![英文图片](ArchFactClient/docs/readme-images/data-extraction-en.png)

### 操作步骤
1. 打开顶部 **数据提取**。
2. 点击右上角 **输入 PDF**，选择报告文件。
3. 在设置区确认：
    - 抽取页码范围（可单页、区间或组合）
    - 抽取模板 / 字段
    - 后处理规则（如有）
4. 点击开始抽取，观察进度与日志。
5. 任务状态变为完成（或带警告完成）后，系统通常会切到 **数据预览**。

> 说明：以下截图均已保存于仓库，避免依赖外部图片服务。
>

---

## 四、页面二：数据预览（浏览核对）
**目的：** 在 PDF 与结构化目录之间对照查看抽取结果，检查文本证据、器物线图、YOLO 裁剪图、彩版关联等。  
<!-- 这是一张图片，ocr 内容为： -->
![](ArchFactClient/docs/readme-images/data-preview-zh.png)

<!-- 这是一张图片，ocr 内容为：不 LOCALHOST:5173 已书停 品口资源口大模型口大模型口 快捷登录 所有书签 ARCHFACT 机器校验 数据预览 数据提取 良洁造址解考古报告之五:文家山(浙江省文物考古研.. ,160/275 考古目录 页面导航 全部字段 内容预览 D  按字 文字工具 美联工具 绘制工具 E敏小 电 放大 第160页100% 良活遗址群考古报告... 类别:玉珠 王新绑定 EVIDENCE FOR 驳回 通过 置信度100%  未市核 质地:白色闪玉 尺寸:高1.1.1.CM 160 器物评倩 青乐色 西湖 尺寸 吉黑色得岩 160/275 关联的网店 161 石砂 完整嘉 经济 文木证据 器物杭州国 形态指述 162 161/275 关联页面 第160页 网注 彩色国板 文本证据 163 M13:12 霄无关联彩图 M13:12 类别:石钱 MEIM 质地:青玉色泥岩 尺寸:制作精细 162/275 第161页.14 第160页24 第161页3/4 -->
![中文图片](ArchFactClient/docs/readme-images/data-preview-zh.png)

<!-- 这是一张图片，ocr 内容为：LOCALHOST5173 已智停 品|口资源口大 所有书签 大模型 快捷登录 ARCHFACT MACHINE VERLFICATION DATA EXTRACTION DATA PREVIEW EN P...160/275 良消造址群考古报告之五:文家山... ARCHAEOLOGICAL C... PAGE NAVIGATOR CONTENT PREVIEW ALL FIELDS LINK TOOL ZOOM OUT ZOOM IN TEXT TOOL PAGE 160.100% 良渚遗址群考古报告.. CALEGORY:玉埃 EVIDENCE FOR CONFIDENCE 100% UNREVLEWED REBIND ACCEPT REJECT TEXTURE:自色闪玉 MEASURAMENIS:高1.1.1 CM 160 ARTIFACT DETAILS SURFACE COLOR 每黑色 M13.12 160/275 161 石线 制作证细 CATEGORY TEXD EVIDENCE 长条形局深体,刀部有您小语造,单面钻孔 MORPHOLOGICAL 162 161/275 RELATED PAGES PAGC 160 LINE DRAWING 163 M13:12 M13:12 CALEGORY:石钱 TEXLURE:青黑色泥岩 MEASUREMENTS:制作精细 162/275 PAGE 161 3/4 PAGE 161`1M 44 PAGE 160-2/4 164 -->
![英文图片](ArchFactClient/docs/readme-images/data-preview-en.png)

### 界面区域
| 区域 | 作用 |
| --- | --- |
| 左侧「页面导航」 | 切换报告页码与缩略图 |
| 中上「内容预览」 | 查看当前页标注、证据框与 YOLO 检测结果 |
| 中下「关联页面」 | 查看线图 / 文本证据 / 裁剪图 / 彩版等关联卡片 |
| 右侧「考古目录」 | 浏览器物卡片，打开「器物详情」核对字段 |


### 操作步骤
1. 在左侧选择页码，确认 PDF 预览加载正常。
2. 在右侧目录中点选器物，查看详情字段（编号、颜色、质地、尺寸、类别、形态描述等）。
3. 在内容预览中点击标注，核对：
    - 文本证据是否对应原文
    - 器物线图 / 裁剪图是否正确
    - 关联关系是否合理
4. 如需调整关系，可使用预览区的 **通过 / 驳回 / 重新绑定**（针对当前证据关系）。

---

## 五、页面三：机器校验（发起核验）
**目的：** 配置断言规则，对当前抽取任务发起机器校验；系统会抽取固定 **18 条**样本进入人工核验。  


<!-- 这是一张图片，ocr 内容为：LOCALHOST:5173 已暂停 品口资源 大模型 快捷登录 所有书签 ARCHFACT-V2-MO.JSON 49.4KB完成 ARCHFACT 导出 中EN 机器校验 数据提取 数据预览 校验规则 校验结果 执行校验 已信用5条规则 18个样本 样本一致性 版本历史 编号唯一性 错误覆盖率 56% 每件器物的唯一编号不得重复. V2 当前版本 准确率 44% 5 ACTIVE ASSERTIONS . 18 FIXED SAMPLES - MO 一致性 44% 可导出 匹配版本MO 颜色空值逻辑 母 全量校验 603件器物 修改前 一致性50%,9条记录需要市核. 原文没有颜色记录时,"NONE不再标记为错误. 修改后 致性44%,10条记录需要市核. 影响 10 错误 一政性 9-101 50%44% 1 9-8 1 通过 错误 图注检查 县4 较早版本 图注编号必须与对应PDF页面中的图片顺序一致. 关系已变化 失效样本 V1 初始版本 2026-07-27 07-54 错误字段分布 5 ACTIVE ASSERTIONS - 18 FIXED SAMPLES MO 匹配版本MO可导出 其他问题 10 添加规则 -->
![中文图片](ArchFactClient/docs/readme-images/machine-verification-zh.png)

<!-- 这是一张图片，ocr 内容为：LOCALHOST:5173 已暂停 品一 所有书签 大模型 快捷登录 厂资源 ARCHFACT INPUT PDF DATA EXTRACTION DATA PREVIEW MACHINE VERIFICATION 中EN PROCESSING PROGRESS PROMPT START EXTRACTION PAGE NAVIGATOR 良渚遗址群步古报告 EXTRACTION TEMPLATE @ CLEAR LOG ELAPSED TIME START TIME INFO第109页样时:YOLO0.13S,区域处理0.00S,语义抽取94.64S, BASLC RESEARCH TEMPLATE MANAGE 2026-07-27 13M 4S SUCCESS第189页处理完成 业EXPORT LOG 文家山 SURFACE COLOR TEXTURE ARTIFACT ID 15:19:27 INFO正在处理第190页 C RETRY 1 FAILED PAGES MORPHOLOGICAL DESCRIPTION MEASUREMENTS INFO 第190页耗时:YOLOO.09S,区域处理0.00S,语义抽取0.01S,合 PROCESSING RATE TIME LEFT 4.2 PAGES/MIN OS SUCCESS第190页处理完成 FIGURE CAPTION CATEGORY COMPLETENESS NO ACTIVE TASK + ADD TEMPLATE 1/275 良消遗址养考古报告之五:文家山(浙江省文物考古研究所)(Z-LIDRARY)(1... CONTENT PREVIEW 100% PAGE 160-100% PNSTRAINTS 良活遗址群老古报告之五:文家山(浙江省文物老吉研究所)Z-LIBRARY)(1).PDF ARTIFACTID TEXT > TEXT > SURFACE C... TEXT > TEXTURE TEXT > MEASURE... MORPHOLO... TEXT > TEXT CATEGORY 2/275 TEXT > FLGURE CA... TEXT COMPLETE... POST-PROCESSING RULES CHINESE TO ARABIC NUMBER CONVERSION AUTOMATICALLY CONVERT CHINESE NUMBERS TO 回 ARABIC NUMERALS. 3/275 EXAMPLE:-百'TO 100 -->
![英文图片](ArchFactClient/docs/readme-images/machine-verification-en.png)

### 界面区域
| 区域 | 作用 |
| --- | --- |
| 左侧「校验规则」 | 启用 / 编辑规则，点击「执行校验」 |
| 中部「校验结果」 | 查看样本一致性、通过/错误统计、错误字段分布 |
| 右侧「版本历史」 | 查看 V1、V2… 各版本摘要与影响对比 |
| 右上角「导出」 | 导出当前可导出的校验版本 JSON |


### 操作步骤
1. 打开顶部 **机器校验**。
2. 在左侧确认需要启用的规则（如编号唯一性、颜色空值逻辑、图注检查等）。
3. 点击 **执行校验**。
4. 系统创建校验会话后，自动进入「数据预览」下的 **审核模式**（固定 18 条样本）。

---

## 六、人工核验 18 条样本
**目的：** 对固定样本逐条给出人工 **通过 / 不通过**，为后续 AI 复核与版本冻结提供人审结论。  
<!-- 这是一张图片，ocr 内容为： -->
![](ArchFactClient/docs/readme-images/data-preview-review-zh.png)

<!-- 这是一张图片，ocr 内容为：已哲停 LOCALHOST:5173 品 快捷登录 大模型 资源 ARCHFACT 完成核验还剩13条 中中EN 机器校验 数据提取 数据预览 P...161/275 良活遗址群考古报告之五:文家山(浙江省文物考古研... 考古目录 页面导航 内容预览 P  捷泰 全部字段 关职工具 绘制工具 电放大 第161页:100% 良清遗址群考古报告.. CAPTION OF 投回 置信度90%未市校 通过 王新绑定 通过 不通过 审核面板 GW幸福限司 160 器物靓剪图 M13.20 ARFFACT LD 自口 送6.8   0.7 五地 民寸 160/275 161 工业形饰 类别 光经验 YOLO 检测到的册... 新面三网形,制作规控总部组长中前空孔 形本批合 162 161/275 关联页面 第161页 图庄 彩色图板 (图3-148元49.6) 163 M13:20 来申核 M13:20 区新金属 类别:五维形饰 质地:闪玉 尺寸:长6.6.6.7.7CM 162/275 第161页-14 第161页:314 第161页.214 -->
![中文图片](ArchFactClient/docs/readme-images/data-preview-review-zh.png)

<!-- 这是一张图片，ocr 内容为：已哲停 LOCALHOST:5173 品 大模型 快捷登录 资源 ARCHFACT COMPLETEVERIFICATION .13 REMALNING 中|EN MACHINE VERIFICATION DATA EXTRACTION DATA PREVIEW P...161/275 良活遗址群老古报告之五:文家山. PAGE NAVIGATOR ARCHAEOLOGICAL C... CONTENT PREVIEW AL FIELDS O SEARCH FOR ZOOM OUT LINK TOOL TEXT TOOL 中  ZOOM IR PAGE  100% 良活遗址群老古报告.. CONFIDENCE 90% UNREVIEWED CAPTION OF ACCEPT VERIFICALION PANEL PASS FAIL 160 SURTACE CELOR 自色 长6.6.6M.7 闪王 160/275 161 玉脸形饰 战国是国形,制作规控,是汉目长,中视济孔 162 161/275 RELATED PAGES PAGE 161 (图3-14B,沙酸49.6) 163 M13: 20 M13:20 CATEGORY:玉锥形饰 TEOXTURE:闪玉 MEASURAMENTS:长6.6CM.直径0.7CM 162/275 PAGE 161-14 PAGE 161 314 -->
![英文图片](ArchFactClient/docs/readme-images/data-preview-review-en.png)

### 操作要点
1. 右上角显示 **完成核验 · 还剩 N 条**，表示尚未审完的条数。
2. 左侧仍可按页浏览 PDF；中间核对证据与关联；右侧在审核面板操作。
3. 对当前样本：
    - 点 **通过**：表示人工认为抽取结果正确
    - 点 **不通过**：选择失败类型（字段错误、文本证据错误、图注匹配错误等），可填备注后确认
4. 逐条审完 18 条，直到剩余为 0。

### 审完后可提交
当 18 条均已人工结论时，右上角变为可点击的 **完成核验**：

<!-- 这是一张图片，ocr 内容为：LOCALHOST:5173 已智停 限口口资源 大模型 快捷登景 所有书签 ARCHFACT 完成核验 中 EN 机器校验 数据提取 数据预览 P...170/275 良渚遗址解考古报告之五:文家山(浙江省文物考古研.. 考古目录 页面导航 内容预览 全部字段 D  检索 9新小 文字工具 绘制工具 关联工具 地放大 第170页:100% 良活遗址群考古报告... 爱回 EVIDENCE  FOR 王新绑定 通过 置信度 69%未审核 审核面板 通过 不通过 匹配版本 MO 168 人机械论冲突 AL金标准复换不确定0% 器物服务国 自动结果故少可匹配的普向情号 泰国绿色 160/275 169 尺寸 质期 YOLO 检测得到的器. 花型 完整堂 形有描述 170 161/275 关联页面 英170页 网址 彩色国版 文本证据 171 版六二,2 霄无关联彩图 版62.2 162/275 第170页:1/4 第170页.24 第170页:3/4 4/4 类别:第 170页 172 -->
![中文图片](ArchFactClient/docs/readme-images/data-preview-review-completed-zh.png)

<!-- 这是一张图片，ocr 内容为：LOCALHOST:5173 已暂停 品                                                                                                     模型 口快捷登录 所有书签 ARCHFACT MACHINE VERIFICATION @中EN COMPLETE VERIFICATION DATA EXTRACTION DATA PREVIEW P...170/275 良诸遗址群考古报告之五:文家山 PAGE NAVIGATOR ARCHAEOLOGICAL C... CONTENT PREVIEW AL FELDS LINK TOOL ZOOMOUT TEXT TOOL O ZOOMIN DRAW  TOOL PAGE 170:100% 良清遍址群考古报告 EVIDENCE FOR CONFIDENCE 69% UNREVIEWED ACCEPT REJECT VERIFICATION PANEL FAIL PASS MATCHING VERSION MO HOMANIAL CONFLICT AI BENCHMARK REVIEW`UNCERTAIN 0% O 有动结果缺少可匹配的餐的编号 SURFACE COLOR 160/275 169 MEECUREMENTS COMPLETENESS MORPHALOGICAL 170 161/275 RELATED PAGES PAGE 170 LINE DRAWING 171 钱股62,2 NO LINKED COLOR PLATE 版六二,2 版62.2 162/275 PAGE 170.214 414 PAGE 170 1/4 PAGE 170 3/4 CATEGORY:PAGO 170 -->
![英文图片](ArchFactClient/docs/readme-images/data-preview-review-completed-en.png)

  
<!-- 这是一张图片，ocr 内容为： -->
![](ArchFactClient/docs/readme-images/data-preview-review-completed-zh.png)  
点击后将启动 DeepSeek AI 复核（不会改写生产抽取记录，只做人机对照）。

---

## 七、AI 复核进行中
**目的：** 大模型对照自动结果、OCR 证据与金标准（若已绑定），给出 AI 结论并与人工结论比对。  
<!-- 这是一张图片，ocr 内容为： -->
![](ArchFactClient/docs/readme-images/data-preview-ai-reviewing-zh.png)

<!-- 这是一张图片，ocr 内容为：已哲停 LOCALHOST:5173 品 快捷登录 大模型 资源 ARCHFACT 并AI复核44% @中EN 机器校验 数据提取 数据预览 P...164/275 良活遗址群考古报告之五:文家山(浙江省文物考古研... 考古目录 页面导航 内容预览 P 提承 全部字段 0缩小 关联工具 文字工具 绘制工具 电放大 第164页:100% 良清遗址解老古报告 EVIDENCE FOR 驳回 置信度100%未市核 通过 王新绑定 M16:18 类别:第167页 文本证据 162 版62.2 类别:第170页 160/275 163 M1:3 类别:型 质地:闪玉 尺寸:直径 19.4 CM YOLO 检测到的涨.... 161/275 M16:10 关联页面 第164页 类别:玉管 质地:白色闪玉 彩色图板 尺寸:离1.9CM.直径0.8CM 165 图3-15AM14平,剖面图 1,3. 15.16.玉管2玉隧孔珠4-11,18.王 BI 珠12,13.陶豆14.购17.玉维形饰 类别:第 185 万 162/275 第162页:2/4 第163页.1A 第163页3/4 414 166 -->
![中文图片](ArchFactClient/docs/readme-images/data-preview-ai-reviewing-zh.png)

<!-- 这是一张图片，ocr 内容为：LOCALHOST5173 已智停 品|口资源口大 大模型 快捷登录 ARCHFACT MACHINE VERLFICATION AL REVIEW67% DATA EXTRACTION 中EN DATA PREVLEW P...164/275 良活遗址群考古报告之五:文家山.. PAGE NAVIGATOR ARCHAEOLOGICAL C... CONTENT PREVIEW AL FIELDS D SEARCH FOR LINK TOOL ZOOM OUT ZOOM IN TEXT TOOL PAGE 164.100% 良活遗址郡考古报告. EVIDENCE FOR CONFIDENCE 100% UNREVLEWED REBIND ACCEPT REJECT M16:18 CATEGORY:PAGE 167 162 版62.2 160/275 CATEGORY:PAGE 170 LINKED ARCHAEALOGI.. 163 M1:3 CATEGORY:照 TEXDURE:闪玉 MEASUREMENTS:应径19.4 CM 164 161/275 M16:10 RELATED PAGES PAGE 1G4 CATEGORY:玉普 TEXTURE:自色闪玉 LINE DRAWING ARTIFACT CROP MEASUREMENTS:高 1.9 CM 165 围3-15AM14平,创西图 1.3. 15.16.玉管2玉隧孔珠4-11,18.王 BI 珠12,13.陶豆14.岗17.玉锥形饰 CATEGORY:PAGE 185 162/275 PAGE 163-34 PAGE 163 - 1M 4/4 PAGE 162.2/4 166 -->
![英文图片](ArchFactClient/docs/readme-images/data-preview-ai-reviewing-en.png)

### 操作要点
1. 右上角显示类似 **AI 复核 xx%**，期间请等待，不要关闭页面。
2. 右侧目录可看到样本通过/不通过标记。
3. 复核结束后：
    - **人机一致**：自动生成版本（如 **V1**），并跳转到 **机器校验** 页展示版本与统计
    - **人机冲突或 AI 无法判断**：留在审核页，需对冲突样本做最终人工确认，再次点击 **完成核验** 后才会冻结版本

---

## 八、查看版本结果并导出
回到 **机器校验** 页后：

1. 在右侧 **版本历史** 中选择 V1 / V2…
2. 中部查看该版本的样本一致性、通过/错误数量、错误字段分布
3. 需要对外交付时，点击右上角 **导出**，下载如 `archfact-V1-M0.json` 的结果文件  


<!-- 这是一张图片，ocr 内容为：LOCALHOST:5173 已暂停 品口资源 大模型 快捷登录 所有书签 ARCHFACT-V2-MO.JSON 49.4KB完成 ARCHFACT 导出 中EN 机器校验 数据提取 数据预览 校验规则 校验结果 执行校验 已信用5条规则 18个样本 样本一致性 版本历史 编号唯一性 错误覆盖率 56% 每件器物的唯一编号不得重复. V2 当前版本 准确率 44% 5 ACTIVE ASSERTIONS . 18 FIXED SAMPLES - MO 一致性 44% 可导出 匹配版本MO 颜色空值逻辑 母 全量校验 603件器物 修改前 一致性50%,9条记录需要市核. 原文没有颜色记录时,"NONE不再标记为错误. 修改后 致性44%,10条记录需要市核. 影响 10 错误 一政性 9-101 50%44% 1 9-8 1 通过 错误 图注检查 县4 较早版本 图注编号必须与对应PDF页面中的图片顺序一致. 关系已变化 失效样本 V1 初始版本 2026-07-27 07-54 错误字段分布 5 ACTIVE ASSERTIONS - 18 FIXED SAMPLES MO 匹配版本MO可导出 其他问题 10 添加规则 -->
![中文图片](ArchFactClient/docs/readme-images/machine-verification-zh.png)

<!-- 这是一张图片，ocr 内容为：已替停 LOCALHOST:5173 品 所有书签 快捷登是 大模型 资源 ARCHFACT OUTPUT DATA EXTRACTION MACHINE VERIFICATION DATA PREVIEW 中 EN VERIFICATION RESULT ASSERTIONS EXECUTE 5 ACTIVE NULES SAMPLE ALIGNMENT VERSION HISTORY 18 SAMPLES IDUNIQUENESS 56% THE UNIQUE ID OF EACH ARTIFACT MUST NOT BE DUPLICATED. ERROR COVERAGE V2 CURRENT VERSION 2026-07-2708-02 PRECISION 44% 5 ACTIVE ASSERTIONS ` 18 S 18 FIXED SAMPLES `MO ALIGNMENT 44% MATCHING VERSION MO COLOR NULL VALUE LOGIC 县 FULL VERIFICATION 603 ARTIFACTS BEFORE ALIGNMENT 50%,9 RECORDS REQUIRE REVIEW. 'NONE" IS HO LONGER LLAGGED AS AN ERROR WHEN THE SOURCE AFTER ALLGNMENT 44%,10 RECORDS REQUIRE REVIEW. HAS NO COLOR RECORD. IMPACT 10 ERROR PASS ALIGNMENT 9-101 50%一44%+ 9-81 PASS ERROR FLGURE CAPTION CHECK 0首 CARLIER THE FIQURE CAPTION NUMBER MUST MATCH THE FIGURE ORDER RELATIONS CHANGED ON THE CORRESPONDING PDF PAGE. V1 INITIAL VERSION 2026-07-2707.54 ERROR FIEID DISTRIBUTION 5 ACTLVE ASSERTLONS . 18 FIXED SAMPLES - MO SIZE PRECISION MATCHING VERALON MO READY TO EXPORT OTHER ADD RULE -->
![英文图片](ArchFactClient/docs/readme-images/machine-verification-en.png)

---
