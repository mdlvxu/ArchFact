# YOLO 本地部署

真实 YOLO 模型通过 `DetectionEngine` 接入，前端仍只调用抽取任务接口。

## 模型目录

```text
models/archaeology-yolo/v1/
├─ best.pt
└─ model.yaml
```

`model.yaml` 至少需要包含训练类别：

```yaml
names:
  0: qiwu
  1: xuhao
  2: tuzhu
  3: muzang
  4: zhengti
```

服务启动时会比较 `best.pt` 内的 `model.names` 和 YAML；不一致时直接拒绝启动，避免类别错位。

## 安装

先从 PyTorch 官方安装页选择与服务器显卡匹配的 CUDA wheel，再安装项目依赖：

```powershell
.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
.venv\Scripts\python.exe -m pip install -e ".[yolo]"
```

## 配置

```dotenv
YOLO_ADAPTER=ultralytics
YOLO_MODEL_PATH=models/archaeology-yolo/v1/best.pt
YOLO_CONFIG_PATH=models/archaeology-yolo/v1/model.yaml
YOLO_DEVICE=0
YOLO_CONFIDENCE=0.30
YOLO_IOU=0.50
YOLO_IMAGE_SIZE=640
YOLO_MODEL_NAME=archaeology-yolo
YOLO_MODEL_VERSION=v1
YOLO_CLASS_MAPPING={"qiwu":"artifact","xuhao":"number","tuzhu":"caption","muzang":"grave_drawing","zhengti":"group"}
REGION_CROP_PADDING=0.01
RELATION_GROUP_CONTAINMENT_THRESHOLD=0.5
```

首版只启动一个 Uvicorn worker。适配器内部使用异步锁串行调用同一个模型实例，避免多个抽取任务并发访问同一 GPU 模型。

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1
```

检测结果统一转换为 `[left, top, right, bottom]` 的 0–1 页面相对坐标，并记录原始类别、置信度、模型名称、版本和推理配置。
