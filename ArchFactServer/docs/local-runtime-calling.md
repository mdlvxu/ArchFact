# ArchFact 本地运行与硬件调用说明

> 整理自 2026-09-13 上午讨论。  
> **范围：** MongoDB 选型、硬件自适应、YOLO / PaddleOCR 的 GPU·CPU 调用关系，以及为何推荐 **CPU OCR + GPU YOLO**。  
> **不包含：** 升级部署最新版 PaddleOCR 3.x（下一阶段调研事项）。

---

## 1. 文档目的

本文说明 ArchFact 在本机跑抽取流水线时，各组件**怎么被调用、哪些会自动跟机器走、哪些必须手配**，并给出当前推荐组合。

相关代码与配置：

| 项 | 位置 |
|---|---|
| 硬件探测与自适应 | `app/core/hardware.py` |
| 配置项默认值 | `app/core/config.py` |
| 本机覆盖配置 | `ArchFactServer/.env` |
| 配置模板 | `ArchFactServer/.env.example` |
| YOLO 接入说明 | `docs/yolo-deployment.md` |

---

## 2. MongoDB：怎么接、用哪一种

### 2.1 空库是否可用

**可以。** 项目不依赖「自带库里已经建好的表」。

- 库结构不是事先导入的 SQL / schema 脚本。
- FastAPI 启动时会 `ping`，再自动创建 `documents`、`extraction_jobs`、`job_events` 等集合的索引（含唯一约束、TTL）。
- 集合本身在第一次写入时出现。

空库里**没有**旧 PDF、抽取任务、核验记录——那些是业务数据，不是「表结构」。要沿用旧结果，需在同一主版本上做 `mongodump` / `mongorestore`。

密钥、YOLO 权重、PaddleOCR 环境在 `.env` 和模型目录里，与换 Mongo 无关。

### 2.2 外置 Mongo 怎么接

1. 安装 **MongoDB 8.0**，监听 `127.0.0.1:27017`。
2. `.env` 保持：
   ```dotenv
   MONGODB_URI=mongodb://localhost:27017
   MONGODB_DATABASE=archfact
   ```
3. 不要和项目自带 `mongod` 抢同一端口：先停自带实例，再启本机安装版。
4. 启动后端；健康检查通过后，空库会按当前代码长出索引。

`start-archfact` 兼容外置库：若本机 `27017` 已在监听，脚本**不会**再拉起项目内便携版 Mongo，而是当作外部实例使用。

### 2.3 便携版 vs 本机安装 vs Docker

| | 项目自带便携版 | 本机安装 MongoDB | Docker |
|---|---|---|---|
| **安装** | 解压即用，启动脚本可自动拉起 | MSI/服务，自配端口与数据目录 | 需 Docker Desktop |
| **与项目配合** | 与 `start-archfact` / `stop-archfact` 一体 | 占满 27017 时脚本当外部库 | `docker compose`（文档方案） |
| **数据位置** | `ArchFactServer\.runtime\data` | 系统数据目录 | 容器卷 |
| **隔离** | 只服务 ArchFact | 可能与其它本机 Mongo 冲突 | 容器隔离 |
| **库表设计** | 相同（启动建索引） | 相同 | 相同 |

本机若出现 `'docker' 不是内部或外部命令`，说明未装 Docker 或不在 PATH，**Docker 方案不可用**，实际只有便携版与本机安装两条路。

### 2.4 推荐

在无 Docker、以本项目开发/全量抽取为主的机器上：**优先继续用项目自带便携版 MongoDB**。

适合改成本机安装的情况：希望开机自启、多项目共用一个 Mongo、或不想把数据放在项目 `.runtime` 下。

换安装版**不会**自动解决内存不足 / 大报告写库压力；也不会带来「更完整的 schema」。

---

## 3. 硬件自适应：`HARDWARE_AUTO_TUNE`

### 3.1 写在哪里

| 层级 | 说明 |
|---|---|
| 运行配置 | `ArchFactServer/.env` 中的 `HARDWARE_AUTO_TUNE=true/false` |
| 模板 | `.env.example` 中有示例行 |
| 代码默认 | `app/core/config.py`：`hardware_auto_tune: bool = True` |

**.env 里可以没有这一行。** 没写时仍按代码默认 **开启**。只有要关掉、或希望配置写明白时，才需要显式加上。修改后须**重启后端**。

### 3.2 开启时会改什么

启动时探测 CPU 核数、内存、NVIDIA CUDA（必要时 `nvidia-smi`）/ Apple MPS，并覆盖 `.env` 里拷过来的部分「吞吐类」数字：

| 旋钮 | 自适应规则 |
|---|---|
| `YOLO_DEVICE` | 有 CUDA → `0`；仅 MPS → `mps`；都没有 → `cpu`（避免无卡机器写着 `0` 起不来） |
| `PADDLE_OCR_WORKERS` / `WORKER_THREADS` | 按核数与内存估算，OCR 进程上限 8 |
| 发现 OCR 并发、页面渲染批次 | 随 OCR 进程数放大 |

**不会**被自适应改动的：置信度、`YOLO_IMAGE_SIZE`、模型路径、API Key 等识别口径与密钥类配置。

本机示例量级（讨论当时）：约 20 核 / 31 GB / RTX 5060 → YOLO `0`，Paddle 约 8 进程 × 若干线程。以启动日志与 `GET /api/v1/health` 为准。

### 3.3 何时关掉

在 `.env` 设 `HARDWARE_AUTO_TUNE=false`，再手写要钉死的值。例如：

- 有 GPU 但强制 YOLO 用 CPU：`YOLO_DEVICE=cpu`
- 多卡指定第 2 块：`YOLO_DEVICE=1`

注意：auto-tune **开启且有 CUDA** 时，会把 YOLO 收到 `0`；仅在 `.env` 写 `YOLO_DEVICE=cpu` **会被盖掉**。要强制 CPU，必须先关 auto-tune。

---

## 4. YOLO：设备如何被调用

YOLO 通过 `DetectionEngine` / Ultralytics 适配器调用，设备由 `YOLO_DEVICE` 决定，并受第 3 节自适应影响。

**默认行为：换电脑一般不用手改 `YOLO_DEVICE`。**

| 场景 | 做法 |
|---|---|
| 有 NVIDIA | 自动 GPU `0` |
| 无 GPU | 自动 `cpu` |
| 强制 CPU / 指定卡号 | `HARDWARE_AUTO_TUNE=false` + 相应 `YOLO_DEVICE` |

安装与模型目录见 `docs/yolo-deployment.md`。当前设计是单 Uvicorn worker + 适配器内串行调用同一模型实例，避免多任务并发抢同一 GPU 模型。

---

## 5. PaddleOCR：自动到哪一步

与 YOLO **不一样**。

### 5.1 会自动调的

在 `HARDWARE_AUTO_TUNE=true` 时，只按 **CPU / 内存** 调吞吐：

- `PADDLE_OCR_WORKERS`
- `PADDLE_OCR_WORKER_THREADS`
- `DISCOVERY_OCR_CONCURRENCY`
- 分页渲染批大小

### 5.2 不会自动切换的（须本机配好）

| 项 | 说明 |
|---|---|
| 是否启用 | `.env`：`OCR_ADAPTER=paddle`（示例默认常为 `disabled`） |
| Python 路径 | `PADDLE_OCR_PYTHON` 指向独立 conda 环境（如 `ppocr`） |
| GPU 还是 CPU | **代码不根据电脑切换**；取决于装的是 `paddlepaddle` 还是 `paddlepaddle-gpu` |
| 语言 / 超时等 | 仍靠 `.env` 与本机环境 |

Worker 长驻进程内创建 `PaddleOCR(...)` 时，不按 YOLO 那套去写 `device=0/cpu`；是否走 GPU 由已安装的 PaddlePaddle 包决定。

### 5.3 和 YOLO 一句话对比

- **YOLO**：有 NVIDIA → GPU，没有 → 自动 CPU。  
- **PaddleOCR**：只自动调**并发规模**；启用与否、Python 路径、CPU/GPU 包，靠本机环境与 `.env`。

---

## 6. 四种 OCR × YOLO 组合

项目**目前不会**在下列四种方案里按机器自动择优。能自动的主要是 YOLO 的设备；OCR 是否 GPU 取决于安装的 Paddle 包。

| 组合 | OCR | YOLO | 典型表现 | 更适合 |
|---|---|---|---|---|
| **A** | CPU | GPU | 分工清楚，吞吐稳 | 有独显、全量抽大报告（当前推荐） |
| **B** | GPU | GPU | 单页 OCR 可能更快，多 worker 易抢显存 | 显存大（约 16GB+）且把 OCR worker 压到 1～2 |
| **C** | CPU | CPU | 最稳、最慢 | 无独显 / 仅验证链路 |
| **D** | GPU | CPU | 收益通常一般 | 基本不推荐（YOLO 更该占 GPU） |

### 6.1 能力边界（现状）

| 能力 | 现状 |
|---|---|
| YOLO GPU/CPU | 可自动 |
| OCR 是否启用 | 手动（`OCR_ADAPTER`） |
| OCR CPU 包 / GPU 包 | 手动（装哪套 paddlepaddle） |
| OCR 进程数 / 线程数 | 可按 CPU/内存自动调（不区分 OCR 是否 GPU） |
| 自动在 A/B/C/D 选最优 | **无** |

---

## 7. 为什么推荐 CPU OCR + GPU YOLO（组合 A）

### 7.1 一般：GPU 版 OCR 是否「更好」

| | GPU 版 Paddle | CPU 版 Paddle |
|---|---|---|
| 单页速度 | 通常更快 | 更慢 |
| 安装 | `paddlepaddle-gpu`，CUDA 须对齐 | `paddlepaddle`（CPU）即可 |
| 显存 | 占用 VRAM | 几乎不占 |
| 稳定性 | 与其它 GPU 任务争抢时可能抖 | 更稳 |

**单页更快 ≠ 整条抽取流水线更好。**

### 7.2 结合 ArchFact 架构

- YOLO：每页一次检测，很适合独占 GPU。  
- PaddleOCR：长驻 **多进程 worker**（自适应后本机常到约 8）。  
  - **CPU OCR**：多进程并行吃多核。  
  - **GPU OCR**：多进程挤同一块显存，还要和 YOLO 抢；在约 **8GB** 级显卡（如 RTX 5060）上，容易变慢甚至 OOM，总吞吐经常不如 A。

因此：

- **8GB 级独显、要跑整本大报告 → 优先 A：CPU OCR + GPU YOLO**  
- **无 GPU → C**  
- **大显存且愿意把 `PADDLE_OCR_WORKERS` 降到 1～2 做对比 → 再试 B**（用整本总耗时判断，不要只看单页）  
- **基本不用 D**

### 7.3 未来 GPU 很强时，是否应改全 GPU

**不一定。** 卡强是必要条件，不是充分条件。

- 仍按默认多 OCR worker 全上 GPU（B）→ 可能仍排队、占显存，效果有可能不如 A，或提升很小。  
- 卡很强，且把 OCR 收成少量 GPU worker、控制与 YOLO 的显存争用，再用整本报告总耗时对比 → B **有机会**超过 A。  

在当前代码与默认自适应策略下：**默认仍优先 A**；换顶级显卡后再专门压测 B，不要假设「全 GPU 一定更快」。

---

## 8. 当前推荐调用结论（摘要）

1. **MongoDB**：无 Docker 时优先项目自带便携版；本机安装亦可，空库靠启动建索引。  
2. **硬件**：默认开启自适应；`.env` 可不写 `HARDWARE_AUTO_TUNE`。  
3. **YOLO**：跟机器自动选 GPU/CPU。  
4. **PaddleOCR**：并发可自动；CPU/GPU 靠安装包，代码不自动切换。  
5. **推荐组合：A — CPU OCR + GPU YOLO**，把显卡留给检测，把多核留给多进程 OCR。

下一阶段若调研升级最新 PaddleOCR，另开文档；不改变本文对「当前推荐硬件分工」的结论。
