import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.services.verification_service import VerificationService


def test_judge_record_sends_page_rules_not_gold_labels() -> None:
    async def go() -> None:
        settings = MagicMock()
        settings.verification_llm_max_concurrency = 1
        settings.verification_llm_timeout_seconds = 5.0
        service = VerificationService(
            settings=settings,
            repository=MagicMock(),
            dispatcher=MagicMock(),
        )
        service._call_llm = AsyncMock(
            return_value={
                "overall_verdict": "passed",
                "confidence": 0.91,
                "reason": "图注与尺寸均符合规则",
                "field_results": [],
            }
        )
        record = {
            "_id": "r-1",
            "fields": {
                "artifact_id": {"value": "T3,4"},
                "figure_caption": {"value": "图一"},
                "measurements": {"value": "口径 6.4 厘米"},
                "texture": {"value": "夹砂陶"},
            },
            "region_ids": ["artifact-1"],
            "relation_ids": ["rel-1"],
        }
        try:
            result = await service._judge_record(
                record=record,
                rules=[
                    {
                        "id": 3,
                        "title": "Figure Caption Check",
                        "description": "图注必须存在",
                        "enabled": True,
                    }
                ],
                expected_label="correct",
                artifact_id_counts={"T3,4": 1},
                visual_context={
                    "artifact_crop_present": True,
                    "color_plate_present": False,
                    "caption_present": True,
                    "number_present": True,
                    "relation_count": 1,
                    "region_kinds": ["artifact"],
                },
            )
        finally:
            await service.close()

        kwargs = service._call_llm.await_args.kwargs
        assert "gold_fields" not in kwargs
        assert "gold_standard" not in kwargs
        assert kwargs["rules"][0]["title"] == "Figure Caption Check"
        assert result["gold_match_status"] is None
        assert result["ai_verdict"] == "passed"

    asyncio.run(go())
