import re
import unicodedata
from typing import Any

_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_OCR_DIGIT_TRANSLATION = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"})
_ARABIC_ITEM_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?P<start>\d{1,3})"
    r"(?:\s*[-–—~～至]\s*(?P<end>\d{1,3}))?"
    r"\s*(?=[.．、]|(?:为|是))"
)
_CHINESE_ITEM_PATTERN = re.compile(
    r"(?<![一二三四五六七八九十百])([一二三四五六七八九十]{1,3})\s*[.．、]"
)
_FIGURE_ITEM_PATTERN = re.compile(
    r"(?:图|fig(?:ure)?\.?)[\s第]*[0-9一二三四五六七八九十百]+"
    r"(?:\s*[-–—]\s*[0-9一二三四五六七八九十百]+)*"
    r"\s*[:：]\s*(\d{1,3})",
    re.IGNORECASE,
)


def trusted_region_text(region: dict[str, Any]) -> str:
    """Return OCR text that passed the region confidence threshold."""

    return str(region.get("text") or "").strip()


def sequence_tokens(value: str) -> set[str]:
    """Normalize a cropped sequence label into comparable numeric tokens."""

    normalized = unicodedata.normalize("NFKC", value).strip()
    compact = re.sub(r"\s+", "", normalized)
    if compact and re.fullmatch(r"[0-9OoIl|]{1,4}", compact):
        compact = compact.translate(_OCR_DIGIT_TRANSLATION)
        return {_canonical_number(compact)}

    tokens = {_canonical_number(token) for token in re.findall(r"\d{1,3}", normalized)}
    chinese_value = _parse_chinese_number(compact)
    if chinese_value is not None:
        tokens.add(str(chinese_value))
    return tokens


def caption_item_tokens(value: str) -> set[str]:
    """Extract explicit subfigure item labels while ignoring figure and artifact numbers."""

    normalized = unicodedata.normalize("NFKC", value)
    tokens: set[str] = set()
    for match in _ARABIC_ITEM_PATTERN.finditer(normalized):
        start = int(match.group("start"))
        end_text = match.group("end")
        if end_text is None:
            tokens.add(str(start))
            continue
        end = int(end_text)
        if start <= end and end - start <= 100:
            tokens.update(str(number) for number in range(start, end + 1))
        else:
            tokens.update({str(start), str(end)})

    for match in _CHINESE_ITEM_PATTERN.finditer(normalized):
        number = _parse_chinese_number(match.group(1))
        if number is not None:
            tokens.add(str(number))

    tokens.update(
        _canonical_number(match.group(1)) for match in _FIGURE_ITEM_PATTERN.finditer(normalized)
    )
    return tokens


def caption_number_match(
    caption: dict[str, Any],
    number: dict[str, Any],
) -> float | None:
    """Return 1/0 for a trusted OCR agreement/conflict, or None when OCR cannot decide."""

    caption_text = trusted_region_text(caption)
    number_text = trusted_region_text(number)
    if not caption_text or not number_text:
        return None
    expected = caption_item_tokens(caption_text)
    observed = sequence_tokens(number_text)
    if not expected or not observed:
        return None
    return 1.0 if expected & observed else 0.0


def sequence_text_score(expected: str, observed: str) -> float:
    """Compare a structured item number with OCR without fuzzy 3/13 false positives."""

    expected_tokens = sequence_tokens(expected)
    observed_tokens = sequence_tokens(observed)
    if not expected_tokens or not observed_tokens:
        return 0.0
    return 1.0 if expected_tokens & observed_tokens else 0.0


def _canonical_number(value: str) -> str:
    return str(int(value))


def _parse_chinese_number(value: str) -> int | None:
    if not value or any(
        character not in _CHINESE_DIGITS and character != "十" for character in value
    ):
        return None
    if "十" not in value:
        if len(value) != 1:
            return None
        return _CHINESE_DIGITS[value]
    if value.count("十") != 1 or len(value) > 3:
        return None
    left, right = value.split("十", 1)
    tens = _CHINESE_DIGITS.get(left, 1) if left else 1
    ones = _CHINESE_DIGITS.get(right, 0) if right else 0
    return tens * 10 + ones
