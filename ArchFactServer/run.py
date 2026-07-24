import os
from pathlib import Path

import uvicorn

from app.core.config import get_settings

if __name__ == "__main__":
    # Keep third-party model settings with this project. This avoids startup
    # failures when the process cannot write to the user's roaming profile.
    os.environ.setdefault(
        "YOLO_CONFIG_DIR",
        str((Path(__file__).resolve().parent / ".runtime" / "model-config").resolve()),
    )
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8080,
        reload=settings.app_env == "development" and settings.yolo_adapter != "ultralytics",
    )
