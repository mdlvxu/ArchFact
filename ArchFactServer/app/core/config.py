from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ArchFact API"
    app_env: Literal["development", "test", "production"] = "development"
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:5173"]

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "archfact"
    gridfs_bucket: str = "pdf_files"
    file_storage_root: Path = Path(".runtime/files")

    extraction_engine: Literal["local", "coze", "coze_http", "llm"] = "local"
    coze_api_base: str = "https://api.coze.cn"
    coze_api_token: str | None = None
    coze_workflow_id: str | None = None
    coze_http_url: str | None = None
    coze_http_token: str | None = None
    coze_http_timeout_seconds: float = Field(default=180, gt=0, le=900)
    llm_provider: str = "deepseek"
    llm_api_base: str = "https://api.deepseek.com"
    llm_api_key: str | None = None
    llm_model: str = "deepseek-v4-flash"
    llm_timeout_seconds: float = Field(default=120, gt=0, le=900)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_concurrency: int = Field(default=3, ge=1, le=16)
    semantic_page_concurrency: int = Field(default=3, ge=1, le=16)
    llm_max_tokens: int = Field(default=4096, ge=256, le=384000)
    llm_input_chunk_chars: int = Field(default=1600, ge=500, le=20000)
    llm_chunk_overlap_chars: int = Field(default=160, ge=0, le=2000)
    llm_request_token_budget: int = Field(default=10000, ge=2000, le=100000)
    llm_max_records_per_chunk: int = Field(default=10, ge=1, le=50)
    llm_thinking: bool = False
    semantic_candidate_filter_enabled: bool = True
    semantic_cache_enabled: bool = True

    # Verification is deliberately configured separately from production extraction.
    # It may reuse the same OpenAI-compatible endpoint/key, but it never writes back to
    # extraction records and a model override can be supplied independently.
    verification_llm_model: str | None = None
    verification_llm_timeout_seconds: float = Field(default=120, gt=0, le=900)
    verification_llm_max_concurrency: int = Field(default=3, ge=1, le=8)
    verification_llm_max_tokens: int = Field(default=1800, ge=256, le=16000)
    gold_dataset_root: Path | None = Path("../参考资料/人工标注/文家山")

    page_render_scale: float = Field(default=1.5, gt=0, le=4)
    page_preparation_batch_size: int = Field(default=8, ge=1, le=64)
    discovery_enabled: bool = True
    discovery_thumbnail_scale: float = Field(default=0.30, ge=0.1, le=1.0)
    discovery_ocr_render_scale: float = Field(default=0.75, ge=0.25, le=1.5)
    discovery_text_preview_chars: int = Field(default=1200, ge=200, le=5000)
    discovery_color_ratio_threshold: float = Field(default=0.035, ge=0, le=1)
    discovery_ocr_concurrency: int = Field(default=2, ge=1, le=8)
    discovery_ocr_max_pages: int = Field(default=80, ge=0, le=1000)
    discovery_max_recalled_pages: int = Field(default=24, ge=1, le=200)
    ocr_adapter: Literal["disabled", "tesseract", "paddle"] = "disabled"
    ocr_policy: Literal["all", "fallback", "disabled"] = "fallback"
    tesseract_command: str = "tesseract"
    ocr_languages: str = "chi_sim+eng"
    ocr_page_segmentation_mode: int = Field(default=3, ge=0, le=13)
    ocr_min_confidence: float = Field(default=0.15, ge=0, le=1)
    paddle_ocr_python: Path | None = None
    paddle_ocr_worker_path: Path = Path("scripts/paddle_ocr_worker.py")
    paddle_ocr_language: str = "ch"
    paddle_ocr_use_angle_cls: bool = False
    paddle_ocr_timeout_seconds: float = Field(default=180, gt=0, le=900)
    paddle_ocr_workers: int = Field(default=2, ge=1, le=4)
    paddle_ocr_worker_threads: int = Field(default=6, ge=1, le=20)
    region_ocr_min_confidence: float = Field(default=0.5, ge=0, le=1)
    region_crop_padding: float = Field(default=0.01, ge=0, le=0.1)
    yolo_adapter: Literal["disabled", "json", "ultralytics"] = "disabled"
    yolo_predictions_path: Path | None = None
    yolo_model_path: Path | None = None
    yolo_config_path: Path | None = None
    yolo_device: str = "0"
    yolo_confidence: float = Field(default=0.30, ge=0, le=1)
    yolo_iou: float = Field(default=0.50, ge=0, le=1)
    yolo_image_size: int = Field(default=640, ge=32, le=4096)
    yolo_model_name: str = "archaeology-yolo"
    yolo_model_version: str = "v1"
    yolo_class_mapping: dict[str, str] = Field(
        default_factory=lambda: {
            "qiwu": "artifact",
            "xuhao": "number",
            "tuzhu": "caption",
            "muzang": "grave_drawing",
            "zhengti": "group",
        }
    )

    relation_matching_min_score: float = Field(default=0.42, ge=0, le=1)
    relation_matching_max_distance: float = Field(default=0.5, gt=0, le=2)
    relation_group_containment_threshold: float = Field(default=0.5, ge=0, le=1)
    relation_layout_weight: float = Field(default=0.35, ge=0)
    relation_distance_weight: float = Field(default=0.30, ge=0)
    relation_overlap_weight: float = Field(default=0.20, ge=0)
    relation_confidence_weight: float = Field(default=0.15, ge=0)

    max_upload_bytes: int = 100 * 1024 * 1024
    max_pdf_pages: int = 1000
    job_event_limit: int = 50

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    def validate_runtime(self) -> None:
        if self.extraction_engine == "coze" and not (self.coze_api_token and self.coze_workflow_id):
            raise ValueError("EXTRACTION_ENGINE=coze 时必须配置 COZE_API_TOKEN 和 COZE_WORKFLOW_ID")
        if self.extraction_engine == "coze_http" and not (
            self.coze_http_url and self.coze_http_token
        ):
            raise ValueError(
                "EXTRACTION_ENGINE=coze_http 时必须配置 COZE_HTTP_URL 和 COZE_HTTP_TOKEN"
            )
        if self.extraction_engine == "llm" and not self.llm_api_key:
            raise ValueError("EXTRACTION_ENGINE=llm 时必须配置 LLM_API_KEY")
        if self.ocr_policy == "all" and self.ocr_adapter == "disabled":
            raise ValueError("OCR_POLICY=all 时不能将 OCR_ADAPTER 设为 disabled")
        if self.ocr_adapter == "paddle":
            if self.paddle_ocr_python is None or not self.paddle_ocr_python.is_file():
                raise ValueError("OCR_ADAPTER=paddle 时必须配置有效的 PADDLE_OCR_PYTHON")
            if not self.paddle_ocr_worker_path.is_file():
                raise ValueError(
                    f"PaddleOCR 工作进程脚本不存在：{self.paddle_ocr_worker_path}"
                )
        if self.yolo_adapter == "json" and self.yolo_predictions_path is None:
            raise ValueError("YOLO_ADAPTER=json 时必须配置 YOLO_PREDICTIONS_PATH")
        if self.yolo_adapter == "ultralytics":
            if self.yolo_model_path is None or self.yolo_config_path is None:
                raise ValueError(
                    "YOLO_ADAPTER=ultralytics 时必须配置 YOLO_MODEL_PATH 和 YOLO_CONFIG_PATH"
                )
            if not self.yolo_model_path.is_file():
                raise ValueError(f"YOLO 模型文件不存在：{self.yolo_model_path}")
            if not self.yolo_config_path.is_file():
                raise ValueError(f"YOLO 配置文件不存在：{self.yolo_config_path}")
            supported_kinds = {
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
            invalid_kinds = sorted(set(self.yolo_class_mapping.values()) - supported_kinds)
            if invalid_kinds:
                raise ValueError(f"YOLO_CLASS_MAPPING 包含不支持的区域类型：{invalid_kinds}")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_runtime()
    return settings
