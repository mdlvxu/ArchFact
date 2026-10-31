import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook

from app.api.v1.quality_evaluations import export_quality_evaluation
from app.core.errors import ConflictError
from app.services.gold_dataset_service import GoldDatasetService, split_figure_references
from app.services.quality_evaluation_service import QualityEvaluationService


def test_baseline_deletion_preserves_associated_evaluations() -> None:
    class Repository:
        referenced = True
        deleted = False

        async def get_gold_dataset(self, dataset_id):
            return {"_id": dataset_id}

        async def has_gold_dataset_references(self, dataset_id):
            return self.referenced

        async def delete_gold_dataset(self, dataset_id):
            self.deleted = True

    async def scenario():
        repository = Repository()
        service = GoldDatasetService(SimpleNamespace(), repository)
        with pytest.raises(ConflictError):
            await service.delete_unreferenced_dataset("protected")
        assert not repository.deleted
        repository.referenced = False
        await service.delete_unreferenced_dataset("unreferenced")
        assert repository.deleted

    asyncio.run(scenario())


def test_quality_controls_pause_resume_and_terminate() -> None:
    class Repository:
        def __init__(self):
            self.run = {"_id": "e1", "job_id": "j1", "status": "running", "logs": []}

        async def get_quality_evaluation_run(self, *, job_id, evaluation_id):
            assert (job_id, evaluation_id) == ("j1", "e1")
            return dict(self.run)

        async def get_quality_evaluation_run_by_id(self, evaluation_id):
            assert evaluation_id == "e1"
            return dict(self.run)

        async def update_quality_evaluation_run(self, _run_id, **fields):
            self.run.update(fields)
            return dict(self.run)

    class Dispatcher:
        cancelled = False

        async def cancel(self, _run_id):
            self.cancelled = True
            return True

    async def scenario():
        repo = Repository()
        dispatcher = Dispatcher()
        service = QualityEvaluationService(repository=repo, dispatcher=dispatcher)
        assert (await service.control("j1", "e1", "pause"))["status"] == "paused"
        waiting = asyncio.create_task(service._checkpoint("e1"))
        await asyncio.sleep(0.02)
        assert not waiting.done()
        assert (await service.control("j1", "e1", "resume"))["status"] == "running"
        await asyncio.wait_for(waiting, timeout=1)
        assert (await service.control("j1", "e1", "terminate"))["status"] == "terminated"
        assert dispatcher.cancelled
        with pytest.raises(asyncio.CancelledError):
            await service._checkpoint("e1")
        with pytest.raises(ConflictError):
            await service.control("j1", "e1", "resume")
        assert [entry["stage"] for entry in repo.run["logs"]] == [
            "paused", "running", "terminated"
        ]

    asyncio.run(scenario())


def test_quality_evaluation_waits_until_rematch_is_applied() -> None:
    class Repository:
        async def get_job(self, job_id):
            return {"_id": job_id, "status": "completed"}

        async def is_job_rematch_applying(self, job_id):
            assert job_id == "j1"
            return True

    async def scenario():
        service = QualityEvaluationService(repository=Repository(), dispatcher=SimpleNamespace())
        with pytest.raises(ConflictError, match="正在应用新的融合结果"):
            await service.create(job_id="j1", gold_dataset_id="gold-1")

    asyncio.run(scenario())


class FakeGoldRepository:
    def __init__(self) -> None:
        self.dataset = None
        self.records = []

    async def get_document(self, _document_id: str) -> dict:
        return {"_id": "doc-1", "filename": "report.pdf"}

    async def get_gold_dataset_for_document(self, **_kwargs):
        return None

    async def list_gold_datasets(self):
        return [self.dataset] if self.dataset else []

    async def replace_gold_dataset(self, *, dataset, records, regions, assets, links):
        self.dataset = dataset
        self.records = records
        assert regions == []
        assert assets == []
        assert links == []


def make_workbook() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["遗迹号", "序号", "尺寸", "颜色"])
    worksheet.append(["M5", 1, "口径 10 厘米", "红褐色"])
    worksheet.append(["M5", 1, "口径 10 厘米", None])
    worksheet.append([None, None, "无主键行", None])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


@pytest.mark.parametrize(
    ("caption", "figures", "plates"),
    [
        ("图3-2F;彩版一四,2", ["图3-2F"], ["彩版一四,2"]),
        ("图一一九：3,彩版一九：2", ["图一一九:3"], ["彩版一九:2"]),
        ("图3-4B；彩图十二：1；图3-5", ["图3-4B", "图3-5"], ["彩图十二:1"]),
        ("none", [], []),
    ],
)
def test_split_figure_and_color_plate_references(caption, figures, plates) -> None:
    assert split_figure_references(caption) == (figures, plates)


def test_workbook_import_stores_split_references_and_preserves_original() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["遗迹号", "序号", "图注"])
    sheet.append(["M1", 4, "图3-2F;彩版一四,2"])
    content = BytesIO()
    workbook.save(content)
    repository = FakeGoldRepository()
    service = GoldDatasetService(SimpleNamespace(), repository)
    asyncio.run(service.import_workbook(
        document_id="doc-1", name="Report", version="1.0", filename="data.xlsx",
        content=content.getvalue(), key_mode="context_sequence",
        field_mapping={"context_id": "遗迹号", "sequence_no": "序号", "figure_caption": "图注"},
    ))
    record = repository.records[0]
    assert record["canonical_artifact_id"] == "M1,4"
    assert record["fields"]["figure_caption"] == record["raw"]["图注"] == "图3-2F;彩版一四,2"
    assert record["figure_references"] == ["图3-2F"]
    assert record["color_plate_references"] == ["彩版一四,2"]


def test_generic_baseline_import_preserves_duplicate_keys_as_ambiguous() -> None:
    repository = FakeGoldRepository()
    service = GoldDatasetService(SimpleNamespace(), repository)
    result = asyncio.run(
        service.import_workbook(
            document_id="doc-1",
            name="Test report",
            version="1.0",
            filename="labels.xlsx",
            content=make_workbook(),
            key_mode="context_sequence",
            field_mapping={
                "context_id": "遗迹号",
                "sequence_no": "序号",
                "measurements": "尺寸",
                "surface_color": "颜色",
            },
        )
    )

    assert result["record_count"] == 2
    assert result["unkeyed_row_count"] == 1
    assert repository.records[0]["canonical_artifact_id"] == "M5,1"
    assert repository.records[0]["duplicate_key_count"] == 2
    assert repository.records[0]["fields"]["measurements"] == "口径 10 厘米"
    assert result["capabilities"] == {
        "has_detection_labels": False,
        "has_relation_labels": False,
    }
    assert any("重复行" in warning for warning in result["warnings"])
    assert result["source_headers"] == ["遗迹号", "序号", "尺寸", "颜色"]
    assert repository.records[0]["raw"]["遗迹号"] == "M5"

    with pytest.raises(ConflictError, match="无需重复导入"):
        asyncio.run(
            service.import_workbook(
                document_id="doc-1",
                name="Again",
                version="2.0",
                filename="other.xlsx",
                content=make_workbook(),
                key_mode="context_sequence",
                field_mapping={"context_id": "遗迹号", "sequence_no": "序号"},
            )
        )


def test_text_only_baseline_does_not_report_missing_image_labels_as_zero_scores() -> None:
    result = QualityEvaluationService._evaluate(
        job={"effective_pages": [1], "requested_pages": [1]},
        document={"page_count": 1},
        records=[],
        gold_records=[],
        regions=[],
        relations=[],
        gold_regions=[],
        gold_links=[],
        pages=[],
        dataset={
            "capabilities": {
                "has_detection_labels": False,
                "has_relation_labels": False,
            }
        },
    )

    assert result["detection_metrics"] == []
    assert result["relation_metrics"] == {}
    assert result["summary"]["detection_macro_f1"] is None
    assert result["summary"]["relation_score"] is None


def test_cross_report_baseline_and_job_history_routes_are_registered() -> None:
    from app.main import app

    paths = app.openapi()["paths"]
    assert "post" in paths["/api/v1/gold-datasets/preview"]
    assert "post" in paths["/api/v1/gold-datasets/import"]
    assert "get" in paths["/api/v1/gold-datasets/{dataset_id}/evaluations"]
    assert "get" in paths["/api/v1/extraction-jobs"]
    assert (
        "get"
        in paths["/api/v1/extraction-jobs/{job_id}/quality-evaluations/{evaluation_id}/export"]
    )


def test_saved_evaluation_exports_human_original_columns_first() -> None:
    class Repository:
        async def get_quality_evaluation_run(self, **_kwargs):
            return {"status": "completed", "dataset_id": "d1"}

        async def get_gold_dataset(self, _dataset_id):
            return {"source_headers": ["遗迹号", "序号", "尺寸"]}

        async def list_gold_records(self, _dataset_id):
            return [
                {
                    "_id": "g1",
                    "source_row": 2,
                    "fields": {"artifact_id": "M1,1"},
                    "raw": {"遗迹号": "M1", "序号": "1", "尺寸": "口径10厘米"},
                }
            ]

        async def list_quality_evaluation_items(self, **_kwargs):
            return (
                [
                    {
                        "gold_record_id": "g1",
                        "record_id": "m1",
                        "match_status": "matched",
                        "source_pages": [3],
                        "field_results": [
                            {"key": "measurements", "actual": "口径10cm", "score": 1.0}
                        ],
                    }
                ],
                1,
            )

    response = asyncio.run(
        export_quality_evaluation(
            job_id="j1",
            evaluation_id="e1",
            container=SimpleNamespace(repository=Repository()),
            kind="detail",
        )
    )

    async def read_response():
        return b"".join([chunk async for chunk in response.body_iterator])

    book = load_workbook(BytesIO(asyncio.run(read_response())), read_only=True)
    sheet = book.active
    assert [sheet.cell(1, col).value for col in range(1, 5)] == [
        "人工Excel行号",
        "人工_遗迹号",
        "人工_序号",
        "人工_尺寸",
    ]
    assert [sheet.cell(2, col).value for col in range(1, 5)] == [2, "M1", "1", "口径10厘米"]
