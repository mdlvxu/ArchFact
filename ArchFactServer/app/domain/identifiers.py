from __future__ import annotations

import re
import unicodedata
from typing import Any

EMPTY_MARKERS = {"", "-", "—", "－", "无", "none", "null", "nan"}


def clean_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = unicodedata.normalize("NFKC", str(value)).strip()
    return None if text.lower() in EMPTY_MARKERS else text


def normalize_identifier(value: Any) -> str:
    text = clean_value(value) or ""
    text = text.replace("，", ",").replace("：", ",").replace(":", ",")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r",+", ",", text).strip(",")
    return text.upper()


def canonical_artifact_id(context: Any, sequence: Any) -> str:
    parts = (clean_value(context), clean_value(sequence))
    return normalize_identifier(",".join(part for part in parts if part))
