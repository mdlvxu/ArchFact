# ArchFact Windows 开发环境配置

本文档用于在新的 Windows 10/11 电脑上配置 ArchFact。基础模式可以完成前后端开发和普通 PDF 文本提取；PaddleOCR、YOLO 和在线大模型均为按需启用。

## 1. 环境要求

基础环境：

- Git
- Node.js 22.13 或更高版本
- pnpm 9.15.9
- Python 3.13（项目最低支持 3.11）
- MongoDB 8.0，或 Docker Desktop
- Microsoft Visual C++ Redistributable 2015–2022 x64

完整 OCR/检测环境还需要：

- Miniconda/Anaconda
- Python 3.10 独立 PaddleOCR 环境
- PaddleOCR 2.9.0、PaddlePaddle 3.0.0
- 使用 GPU YOLO 时需要 NVIDIA 驱动及 CUDA 12.8 兼容的 PyTorch
- `models/archaeology-yolo/v1/best.pt` 自定义模型权重

## 2. 获取源码

```powershell
git clone https://github.com/mdlvxu/ArchFact.git
Set-Location ArchFact
```

仓库不会包含 `.env`、数据库、上传文件、PaddleOCR 缓存、人工标注数据及 YOLO 权重。需要保留旧电脑数据时，请另外通过安全方式迁移。

## 3. 配置 MongoDB

### 方案 A：Docker

```powershell
Set-Location ArchFactServer
docker compose up -d
docker compose ps
Set-Location ..
```

该方式使用 `mongo:8.0` 和 Docker 命名卷 `archfact-mongo`。

### 方案 B：本机或便携版 MongoDB 8.0

安装 MongoDB 8.0，或从 MongoDB 官网下载 Windows ZIP 并解压。准备数据和日志目录：

```powershell
New-Item -ItemType Directory -Force ArchFactServer\.runtime\data | Out-Null
New-Item -ItemType Directory -Force ArchFactServer\.runtime\logs | Out-Null
```

启动时将下面的 `<MONGODB_HOME>` 替换为实际解压路径：

```powershell
& "<MONGODB_HOME>\bin\mongod.exe" `
  --dbpath "$PWD\ArchFactServer\.runtime\data" `
  --bind_ip 127.0.0.1 `
  --port 27017 `
  --logpath "$PWD\ArchFactServer\.runtime\logs\mongodb.log" `
  --logappend
```

数据库验证：

```powershell
Test-NetConnection 127.0.0.1 -Port 27017
```

若迁移旧数据，优先使用相同 MongoDB 主版本执行 `mongodump`/`mongorestore`。直接复制 WiredTiger 数据目录前必须先正常停止 MongoDB。

## 4. 配置后端

创建虚拟环境并安装依赖：

```powershell
Set-Location ArchFactServer
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

基础开发可保留以下设置：

```dotenv
MONGODB_URI=mongodb://localhost:27017
MONGODB_DATABASE=archfact
FILE_STORAGE_ROOT=.runtime/files
EXTRACTION_ENGINE=local
OCR_ADAPTER=disabled
YOLO_ADAPTER=disabled
```

启动后端：

```powershell
.\.venv\Scripts\python.exe .\run.py
```

访问 http://localhost:8080/docs，或执行：

```powershell
Invoke-RestMethod http://localhost:8080/api/v1/health
```

## 5. 配置前端

另开 PowerShell：

```powershell
Set-Location ArchFactClient
npm install --global corepack
corepack prepare pnpm@9.15.9 --activate
pnpm install --frozen-lockfile
Copy-Item .env.example .env
pnpm dev
```

打开 http://localhost:5173。开发服务器会将 `/api` 代理到 `http://localhost:8080`。

## 6. 启用 PaddleOCR

PaddlePaddle 不应安装进 FastAPI 的 Python 3.13 环境。创建独立 Python 3.10 环境：

```powershell
conda create -n ppocr python=3.10 -y
conda activate ppocr
python -m pip install --upgrade pip
python -m pip install paddleocr==2.9.0 paddlepaddle==3.0.0
python -c "from paddleocr import PaddleOCR; print('PaddleOCR OK')"
```

在 `ArchFactServer/.env` 中配置实际路径：

```dotenv
OCR_ADAPTER=paddle
PADDLE_OCR_PYTHON=C:/Users/<USER>/miniconda3/envs/ppocr/python.exe
PADDLE_OCR_WORKER_PATH=app/services/paddle_ocr_worker.py
PADDLE_OCR_LANGUAGE=ch
PADDLE_OCR_USE_ANGLE_CLS=false
```

首次运行会下载中文 OCR 模型到用户目录下的 `.paddleocr`。离线电脑需要提前下载或从旧电脑复制该缓存。

如果使用 Tesseract 作为替代方案，请安装 Tesseract 5 和 `chi_sim`、`eng` 语言包，然后配置：

```dotenv
OCR_ADAPTER=tesseract
TESSERACT_COMMAND=tesseract
OCR_LANGUAGES=chi_sim+eng
```

## 7. 启用 YOLO

先根据显卡安装 PyTorch。当前开发环境使用 CUDA 12.8：

```powershell
Set-Location ArchFactServer
.\.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -e ".[yolo]"
```

将模型权重放到：

```text
ArchFactServer/models/archaeology-yolo/v1/best.pt
```

随后修改 `.env`：

```dotenv
YOLO_ADAPTER=ultralytics
YOLO_MODEL_PATH=models/archaeology-yolo/v1/best.pt
YOLO_CONFIG_PATH=models/archaeology-yolo/v1/model.yaml
YOLO_DEVICE=0
```

验证 CUDA：

```powershell
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

没有兼容 GPU 时可将 `YOLO_DEVICE=cpu`，或者继续使用 `YOLO_ADAPTER=disabled`。

## 8. 配置在线语义抽取

默认 `EXTRACTION_ENGINE=local` 不需要外部密钥。启用 DeepSeek 等 OpenAI 兼容接口时，在 `ArchFactServer/.env` 中填写：

```dotenv
EXTRACTION_ENGINE=llm
LLM_PROVIDER=deepseek
LLM_API_BASE=https://api.deepseek.com
LLM_API_KEY=<YOUR_API_KEY>
LLM_MODEL=<AVAILABLE_MODEL_NAME>
```

也可按 `.env.example` 配置 Coze。真实 API Key 只能保存在 `.env`，不要提交到 Git。

## 9. 迁移旧电脑的本地数据

按需通过安全通道迁移：

- `ArchFactServer/.env`
- MongoDB 的 `archfact` 数据库和 GridFS 文件
- `ArchFactServer/.runtime/files`
- `ArchFactServer/models/archaeology-yolo/v1/best.pt`
- `%USERPROFILE%/.paddleocr`
- 人工标注数据、训练数据

不要复制：

- `ArchFactClient/node_modules`
- `ArchFactClient/dist`
- `ArchFactServer/.venv`
- Conda 环境目录
- Python、Vite、测试和日志缓存

依赖环境应在新电脑重新安装。

## 10. 完整验证

```powershell
# 后端
Set-Location ArchFactServer
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .

# 前端
Set-Location ..\ArchFactClient
pnpm type-check
pnpm lint:check
pnpm test:run
pnpm build

# 联调
Invoke-RestMethod http://localhost:8080/api/v1/health
Invoke-RestMethod http://localhost:5173/api/v1/health
```

正常启动顺序为：MongoDB → FastAPI → Vite。
