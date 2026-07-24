import re
from typing import Any

from app.models.schemas import PostProcessingRuleSpec


class PostProcessor:
    _punctuation_translation = str.maketrans(
        {
            "，": ",",
            "。": ".",
            "：": ":",
            "；": ";",
            "（": "(",
            "）": ")",
        }
    )
    _chinese_digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    _chinese_units = {"十": 10, "百": 100, "千": 1000, "万": 10_000, "亿": 100_000_000}
    _chinese_number_pattern = re.compile(r"[零〇一二两三四五六七八九十百千万亿]+")

    def apply(
        self,
        records: list[dict[str, Any]],
        rules: list[PostProcessingRuleSpec],
    ) -> list[dict[str, Any]]:
        enabled = {rule.key for rule in rules}
        for record in records:
            for field in record.get("fields", {}).values():
                value = field.get("value")
                if not isinstance(value, str):
                    continue
                if "chinese_number_to_arabic" in enabled:
                    value = self._chinese_number_pattern.sub(
                        lambda match: str(self._parse_chinese_number(match.group(0))),
                        value,
                    )
                if "space_removal" in enabled:
                    value = re.sub(r"\s+", " ", value).strip()
                if "punctuation_normalization" in enabled:
                    value = value.translate(self._punctuation_translation)
                if "unit_standardization" in enabled:
                    value = re.sub(r"(?<=\d)\s*(厘米|公分)", " cm", value)
                if "date_formatting" in enabled:
                    value = self._format_dates(value)
                field["value"] = value
        return records

    @classmethod
    def _parse_chinese_number(cls, value: str) -> int:
        if not any(character in cls._chinese_units for character in value):
            return int("".join(str(cls._chinese_digits[character]) for character in value))

        total = 0
        section = 0
        number = 0
        for character in value:
            if character in cls._chinese_digits:
                number = cls._chinese_digits[character]
                continue

            unit = cls._chinese_units[character]
            if unit < 10_000:
                section += (number or 1) * unit
            else:
                total += (section + number) * unit
                section = 0
            number = 0
        return total + section + number

    @staticmethod
    def _format_dates(value: str) -> str:
        def replace_date(match: re.Match[str]) -> str:
            year, month, day = (int(part) for part in match.groups())
            return f"{year:04d}-{month:02d}-{day:02d}"

        value = re.sub(r"(\d{4})年(\d{1,2})月(\d{1,2})日", replace_date, value)
        return re.sub(r"(\d{4})[/.](\d{1,2})[/.](\d{1,2})", replace_date, value)
