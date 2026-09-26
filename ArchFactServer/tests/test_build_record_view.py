from datetime import datetime, timezone

from app.application.views import build_record_view


def test_build_record_view_tolerates_missing_created_at() -> None:
    view = build_record_view(
        {
            "_id": "rec_missing_ts",
            "job_id": "job-1",
            "record_type": "artifact",
            "source_pages": [12],
            "fields": {},
        },
        compact=True,
    )

    assert view.id == "rec_missing_ts"
    assert isinstance(view.created_at, datetime)


def test_build_record_view_prefers_updated_at_fallback() -> None:
    stamp = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    view = build_record_view(
        {
            "_id": "rec_updated",
            "job_id": "job-1",
            "source_pages": [1],
            "fields": {},
            "updated_at": stamp,
        },
        compact=True,
    )

    assert view.created_at == stamp


def test_build_record_view_keeps_text_evidence_in_compact_mode() -> None:
    view = build_record_view(
        {
            "_id": "rec_evidence",
            "job_id": "job-1",
            "source_pages": [12],
            "fields": {},
            "region_ids": ["reg_1"],
            "text_evidence": [
                {
                    "page": 12,
                    "quote": "泥质灰陶罐",
                    "bbox": [0.1, 0.2, 0.3, 0.4],
                    "region_id": "reg_1",
                    "kind": "text",
                    "crop_object_key": "omit-me",
                }
            ],
            "linkage": {
                "identity": {"artifact_id": "M1:1"},
                "visual_link": {"evidence": [{"page": 12}], "evidence_block_ids": ["b1"]},
            },
        },
        compact=True,
    )

    assert view.region_ids == ["reg_1"]
    assert view.text_evidence[0].quote == "泥质灰陶罐"
    assert view.text_evidence[0].region_id == "reg_1"
    assert view.linkage.visual_link.evidence == []
