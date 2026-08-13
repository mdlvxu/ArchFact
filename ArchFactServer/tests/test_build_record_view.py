from datetime import datetime, timezone

from app.api.v1.extraction_jobs import build_record_view


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
