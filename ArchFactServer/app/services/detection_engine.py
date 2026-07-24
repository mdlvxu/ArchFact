import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.core.config import Settings
from app.core.errors import DomainError


@dataclass(frozen=True, slots=True)
class PageImageInput:
    job_id: str
    document_id: str
    page_no: int
    image_path: Path
    object_key: str
    width: int
    height: int


class DetectionEngine(Protocol):
    enabled: bool
    provider: str
    model: str
    version: str
    config: dict[str, Any]

    async def detect(self, page: PageImageInput) -> list[dict[str, Any]]: ...


class DisabledYoloDetectionEngine:
    enabled = False
    provider = "archfact"
    model = "disabled-yolo"
    version = "1"
    config: dict[str, Any] = {"adapter": "disabled"}

    async def detect(self, page: PageImageInput) -> list[dict[str, Any]]:
        del page
        return []


class JsonYoloDetectionEngine:
    """Development adapter that consumes deterministic YOLO-like JSON predictions."""

    enabled = True
    provider = "yolo"
    model = "json-predictions"
    version = "1"
    _class_kinds = {
        0: "artifact",
        1: "number",
        2: "caption",
        3: "grave_drawing",
        4: "group",
    }
    _supported_kinds = {
        "text",
        "line_drawing",
        "color_plate",
        "artifact",
        "caption",
        "number",
        "group",
        "grave_drawing",
        "other",
    }

    def __init__(self, predictions_path: Path) -> None:
        try:
            payload = json.loads(predictions_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取 YOLO JSON 预测文件：{exc}") from exc
        pages = payload.get("pages") if isinstance(payload, dict) else None
        if not isinstance(pages, dict):
            raise ValueError("YOLO JSON 预测文件必须包含 pages 对象")
        self._pages = pages
        self.config = {
            "adapter": "json",
            "predictions_path": str(predictions_path),
        }

    async def detect(self, page: PageImageInput) -> list[dict[str, Any]]:
        predictions = self._pages.get(str(page.page_no), [])
        if not isinstance(predictions, list):
            raise DomainError("当前页 YOLO JSON 预测必须是数组", code=5035, status_code=500)

        regions = []
        for index, prediction in enumerate(predictions):
            if not isinstance(prediction, dict):
                continue
            bbox = prediction.get("bbox")
            if not self._is_bbox(bbox):
                raise DomainError(
                    f"第 {page.page_no} 页第 {index + 1} 个 YOLO 检测框无效",
                    code=5036,
                    status_code=500,
                )
            class_id = prediction.get("class_id")
            kind = prediction.get("kind") or self._class_kinds.get(class_id, "other")
            if kind not in self._supported_kinds:
                raise DomainError(
                    f"第 {page.page_no} 页第 {index + 1} 个 YOLO 类别无效：{kind}",
                    code=5037,
                    status_code=500,
                )
            confidence = prediction.get("confidence")
            if confidence is not None and (
                not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1
            ):
                raise DomainError(
                    f"第 {page.page_no} 页第 {index + 1} 个 YOLO 置信度无效",
                    code=5038,
                    status_code=500,
                )
            regions.append(
                {
                    "id": f"reg_{page.job_id}_{page.page_no}_yolo_{index}",
                    "kind": kind,
                    "bbox": [float(value) for value in bbox],
                    "bbox_px": [
                        float(bbox[0]) * page.width,
                        float(bbox[1]) * page.height,
                        float(bbox[2]) * page.width,
                        float(bbox[3]) * page.height,
                    ],
                    "text": "",
                    "confidence": confidence,
                    "source": "yolo_json",
                    "image_id": None,
                    "crop_object_key": None,
                    "class_id": class_id,
                }
            )
        return regions

    @staticmethod
    def _is_bbox(bbox: Any) -> bool:
        return (
            isinstance(bbox, list)
            and len(bbox) == 4
            and all(isinstance(value, (int, float)) and 0 <= value <= 1 for value in bbox)
            and bbox[0] < bbox[2]
            and bbox[1] < bbox[3]
        )


class UltralyticsYoloDetectionEngine:
    """Runs a trusted local Ultralytics checkpoint behind the stable detector boundary."""

    enabled = True
    provider = "ultralytics"
    _supported_kinds = {
        "text",
        "line_drawing",
        "color_plate",
        "artifact",
        "caption",
        "number",
        "group",
        "grave_drawing",
        "other",
    }

    def __init__(
        self,
        *,
        model_path: Path,
        config_path: Path,
        device: str,
        confidence: float,
        iou: float,
        image_size: int,
        model_name: str,
        model_version: str,
        class_mapping: dict[str, str],
    ) -> None:
        if not model_path.is_file():
            raise ValueError(f"YOLO 模型文件不存在：{model_path}")
        if not config_path.is_file():
            raise ValueError(f"YOLO 配置文件不存在：{config_path}")

        configured_names = _read_yolo_class_names(config_path)
        backend = _load_ultralytics_model(model_path)
        task = getattr(backend, "task", None)
        if task != "detect":
            raise ValueError(f"当前 YOLO 适配器只支持 detect 模型，实际任务为：{task}")

        model_names = _normalize_class_names(getattr(backend, "names", None))
        if model_names != configured_names:
            raise ValueError(
                f"YOLO 模型类别与配置文件不一致：model={model_names}, config={configured_names}"
            )

        invalid_kinds = sorted(set(class_mapping.values()) - self._supported_kinds)
        if invalid_kinds:
            raise ValueError(f"YOLO 类别映射包含不支持的区域类型：{invalid_kinds}")

        self.model = model_name
        self.version = model_version
        self.config = {
            "adapter": "ultralytics",
            "device": device,
            "confidence": confidence,
            "iou": iou,
            "image_size": image_size,
            "class_mapping": dict(class_mapping),
        }
        self._backend = backend
        self._device = device
        self._confidence = confidence
        self._iou = iou
        self._image_size = image_size
        self._class_names = model_names
        self._kind_by_name = {
            class_name: class_mapping.get(class_name, "other")
            for class_name in model_names.values()
        }
        self._inference_lock = asyncio.Lock()

    async def detect(self, page: PageImageInput) -> list[dict[str, Any]]:
        if not page.image_path.is_file():
            raise DomainError(
                f"第 {page.page_no} 页 YOLO 输入图片不存在：{page.image_path}",
                code=5039,
                status_code=500,
            )
        await self._inference_lock.acquire()
        worker = asyncio.create_task(asyncio.to_thread(self._detect_sync, page))
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError:
            asyncio.create_task(self._release_lock_after_worker(worker))
            raise
        except DomainError:
            self._inference_lock.release()
            raise
        except Exception as exc:
            self._inference_lock.release()
            raise DomainError(
                f"第 {page.page_no} 页 YOLO 推理失败：{exc}",
                code=5040,
                status_code=500,
            ) from exc
        self._inference_lock.release()
        return result

    async def _release_lock_after_worker(
        self,
        worker: asyncio.Task[list[dict[str, Any]]],
    ) -> None:
        """Discard a cancelled inference result but keep the shared model serialized."""
        try:
            await worker
        except BaseException:
            pass
        finally:
            self._inference_lock.release()

    def _detect_sync(self, page: PageImageInput) -> list[dict[str, Any]]:
        results = self._backend.predict(
            source=str(page.image_path),
            device=self._device,
            imgsz=self._image_size,
            conf=self._confidence,
            iou=self._iou,
            verbose=False,
        )
        if not results:
            return []

        result = results[0]
        original_shape = getattr(result, "orig_shape", (page.height, page.width))
        image_height, image_width = (int(original_shape[0]), int(original_shape[1]))
        if (image_width, image_height) != (page.width, page.height):
            raise DomainError(
                "YOLO 输入图片尺寸与分页元数据不一致："
                f"image={image_width}x{image_height}, metadata={page.width}x{page.height}",
                code=5041,
                status_code=500,
            )

        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return []
        coordinates = boxes.xyxy.cpu().tolist()
        class_ids = boxes.cls.cpu().tolist()
        confidences = boxes.conf.cpu().tolist()
        if not (len(coordinates) == len(class_ids) == len(confidences)):
            raise DomainError("YOLO 检测结果字段数量不一致", code=5042, status_code=500)

        regions: list[dict[str, Any]] = []
        for index, (coordinate, class_value, confidence_value) in enumerate(
            zip(coordinates, class_ids, confidences, strict=True)
        ):
            if len(coordinate) != 4:
                continue
            x1, y1, x2, y2 = (float(value) for value in coordinate)
            x1 = min(max(x1, 0.0), float(image_width))
            y1 = min(max(y1, 0.0), float(image_height))
            x2 = min(max(x2, 0.0), float(image_width))
            y2 = min(max(y2, 0.0), float(image_height))
            if x1 >= x2 or y1 >= y2:
                continue

            class_id = int(class_value)
            class_name = self._class_names.get(class_id)
            if class_name is None:
                raise DomainError(
                    f"YOLO 返回未知类别 ID：{class_id}",
                    code=5043,
                    status_code=500,
                )
            regions.append(
                {
                    "id": f"reg_{page.job_id}_{page.page_no}_yolo_{index}",
                    "kind": self._kind_by_name[class_name],
                    "bbox": [
                        x1 / image_width,
                        y1 / image_height,
                        x2 / image_width,
                        y2 / image_height,
                    ],
                    "bbox_px": [x1, y1, x2, y2],
                    "text": "",
                    "confidence": float(confidence_value),
                    "source": "yolo_ultralytics",
                    "image_id": None,
                    "crop_object_key": None,
                    "class_id": class_id,
                    "class_name": class_name,
                }
            )
        return regions


def _load_ultralytics_model(model_path: Path) -> Any:
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise ValueError('Ultralytics 运行依赖未安装，请先执行 pip install -e ".[yolo]"') from exc
    return YOLO(str(model_path))


def _read_yolo_class_names(config_path: Path) -> dict[int, str]:
    try:
        import yaml
    except ImportError as exc:
        raise ValueError("读取 YOLO 配置需要 PyYAML") from exc
    try:
        content = config_path.read_bytes()
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("gb18030")
        payload = yaml.safe_load(text)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"无法读取 YOLO 配置文件：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("YOLO 配置文件必须是 YAML 对象")
    return _normalize_class_names(payload.get("names"))


def _normalize_class_names(names: Any) -> dict[int, str]:
    if isinstance(names, list):
        normalized = {index: str(name) for index, name in enumerate(names)}
    elif isinstance(names, dict):
        try:
            normalized = {int(class_id): str(name) for class_id, name in names.items()}
        except (TypeError, ValueError) as exc:
            raise ValueError("YOLO 类别 ID 必须是整数") from exc
    else:
        raise ValueError("YOLO 配置缺少 names 类别定义")

    ordered = dict(sorted(normalized.items()))
    if not ordered or list(ordered) != list(range(len(ordered))):
        raise ValueError("YOLO 类别 ID 必须从 0 开始连续编号")
    if any(not name.strip() for name in ordered.values()):
        raise ValueError("YOLO 类别名称不能为空")
    return ordered


def build_detection_engine(settings: Settings) -> DetectionEngine:
    if settings.yolo_adapter == "json":
        if settings.yolo_predictions_path is None:
            raise ValueError("YOLO JSON 适配器缺少预测文件路径")
        return JsonYoloDetectionEngine(settings.yolo_predictions_path)
    if settings.yolo_adapter == "ultralytics":
        if settings.yolo_model_path is None or settings.yolo_config_path is None:
            raise ValueError("YOLO Ultralytics 适配器缺少模型或配置文件路径")
        return UltralyticsYoloDetectionEngine(
            model_path=settings.yolo_model_path,
            config_path=settings.yolo_config_path,
            device=settings.yolo_device,
            confidence=settings.yolo_confidence,
            iou=settings.yolo_iou,
            image_size=settings.yolo_image_size,
            model_name=settings.yolo_model_name,
            model_version=settings.yolo_model_version,
            class_mapping=settings.yolo_class_mapping,
        )
    return DisabledYoloDetectionEngine()
