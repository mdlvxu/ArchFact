from typing import Any


def relation_key(relation: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(relation.get("source_region_id", "")),
        str(relation.get("target_region_id", "")),
        str(relation.get("relation_type", "related_to")),
    )
