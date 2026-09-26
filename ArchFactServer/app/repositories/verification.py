from __future__ import annotations

import random
import secrets
from typing import Any
from uuid import uuid4

from pymongo import DESCENDING, ReturnDocument, UpdateOne
from pymongo.errors import DuplicateKeyError

from app.core.errors import ConflictError, DomainError, NotFoundError
from app.domain.time import utc_now
from app.domain.verification_sampling import select_balanced_verification_sample


class VerificationPersistence:
    """Verification cohorts, sessions, AI runs, and versions."""

    @staticmethod
    def _experiment_filter(experiment_id: str | None) -> dict[str, Any]:
        """Keep restored pre-experiment data readable as one legacy history."""

        if not experiment_id or experiment_id == "legacy":
            return {"$or": [{"experiment_id": {"$exists": False}}, {"experiment_id": "legacy"}]}
        return {"experiment_id": experiment_id}

    @staticmethod
    def _count_crop_bound_entities(records: list[dict[str, Any]]) -> int:
        """Count the same entity-level card scope used by machine verification.

        A job keeps OCR provenance records even when no artifact crop was linked,
        and one artifact can have several page records.  Neither should inflate
        an experiment's artifact total.  Keep this small scope calculation here
        rather than using ``count_documents`` so the experiment header, run
        progress, human sample, and exported report describe the same set.
        """

        crop_bound_entity_ids = {
            str(record.get("entity_id"))
            for record in records
            if record.get("entity_id")
            and (record.get("primary_artifact_region_id") or record.get("thumbnail_region_id"))
        }
        keys: set[str] = set()
        for index, record in enumerate(records):
            entity_id = record.get("entity_id")
            has_own_crop = bool(
                record.get("primary_artifact_region_id") or record.get("thumbnail_region_id")
            )
            has_entity_crop = bool(entity_id and str(entity_id) in crop_bound_entity_ids)
            if not has_own_crop and not has_entity_crop:
                continue
            keys.add(
                f"entity:{entity_id}" if entity_id else f"record:{record.get('_id', index)}"
            )
        return len(keys)

    async def _verification_artifact_count(self, job_id: str) -> int:
        """Return the card count eligible for full machine verification."""

        return self._count_crop_bound_entities(await self.list_job_records(job_id))

    async def _refresh_experiment_artifact_count(
        self,
        experiment: dict[str, Any],
    ) -> dict[str, Any]:
        """Backfill legacy raw-record totals without requiring re-extraction."""

        job_id = str(experiment["job_id"])
        experiment_id = str(experiment.get("_id", "legacy"))
        latest_run = await self._db.machine_verification_runs.find_one(
            {
                "job_id": job_id,
                "experiment_id": experiment_id,
                "total_artifacts": {"$gt": 0},
            },
            sort=[("created_at", DESCENDING)],
            projection={"total_artifacts": 1},
        )
        count = int((latest_run or {}).get("total_artifacts") or 0)
        if count <= 0:
            count = await self._verification_artifact_count(job_id)

        refreshed = {**experiment, "artifact_count": count}
        if experiment_id != "legacy" and experiment.get("artifact_count") != count:
            await self._db.verification_experiments.update_one(
                {"_id": experiment_id, "job_id": job_id},
                {
                    "$set": {
                        "artifact_count": count,
                        "source_snapshot.verification_artifact_count": count,
                    }
                },
            )
        return refreshed

    async def get_active_verification_experiment(self, job_id: str) -> dict[str, Any]:
        job = await self.get_job(job_id)
        experiment_id = job.get("active_verification_experiment_id")
        if not experiment_id:
            return await self._refresh_experiment_artifact_count({
                "_id": "legacy",
                "job_id": job_id,
                "name": "历史校验",
                "sequence": 0,
                "status": "legacy",
                "matching_version_id": job.get("active_matching_version_id", "M0"),
                "artifact_count": 0,
                "created_at": job.get("created_at", utc_now()),
            })
        experiment = await self._db.verification_experiments.find_one(
            {"_id": experiment_id, "job_id": job_id}
        )
        if experiment is None:
            # A partially restored job must not inherit an unrelated prior sample.
            return await self._refresh_experiment_artifact_count({
                "_id": "legacy",
                "job_id": job_id,
                "name": "历史校验",
                "sequence": 0,
                "status": "legacy",
                "matching_version_id": job.get("active_matching_version_id", "M0"),
                "artifact_count": 0,
                "created_at": job.get("created_at", utc_now()),
            })
        return await self._refresh_experiment_artifact_count(experiment)

    async def list_verification_experiments(self, job_id: str) -> list[dict[str, Any]]:
        """List active and archived experiments without changing the active one."""

        job = await self.get_job(job_id)
        experiments = await self._db.verification_experiments.find(
            {"job_id": job_id}
        ).sort("sequence", DESCENDING).to_list(length=1000)
        legacy_version_count = await self._db.verification_versions.count_documents(
            {"job_id": job_id, **self._experiment_filter("legacy")}
        )
        if legacy_version_count or not experiments:
            experiments.append(
                {
                    "_id": "legacy",
                    "job_id": job_id,
                    "name": "历史校验",
                    "sequence": 0,
                    "status": "legacy",
                    "matching_version_id": job.get("active_matching_version_id", "M0"),
                    "artifact_count": 0,
                    "created_at": job.get("created_at", utc_now()),
                }
            )
        return [
            await self._refresh_experiment_artifact_count(experiment)
            for experiment in experiments
        ]

    async def create_verification_experiment(self, job_id: str) -> dict[str, Any]:
        """Open a fresh blind cohort after the active experiment has completed V1.

        Extraction records and PDF relations are deliberately not copied or deleted.
        A new experiment merely freezes a new verification boundary over that data.
        Creating that boundary is irreversible for the prior experiment: it becomes
        read-only, so unfinished V1 reviews must never be silently archived.
        """

        job = await self.get_job(job_id)
        active_run = await self.get_active_machine_verification_run(job_id)
        if active_run is not None:
            raise ConflictError("请先暂停或完成当前全量机器校验，再新建实验基线")
        active_experiment_id = job.get("active_verification_experiment_id")
        if active_experiment_id:
            active_experiment = await self._db.verification_experiments.find_one(
                {"_id": active_experiment_id, "job_id": job_id, "status": "active"}
            )
            if active_experiment is not None:
                completed_v1 = await self._db.verification_versions.find_one(
                    {
                        "job_id": job_id,
                        "experiment_id": active_experiment_id,
                        "version": 1,
                    },
                    projection={"_id": 1},
                )
                if completed_v1 is None:
                    active_session = await self.get_active_verification_session(
                        job_id, experiment_id=active_experiment_id
                    )
                    if active_session is not None:
                        raise ConflictError(
                            f"{active_experiment.get('name', '当前实验')} 的 V1 尚待完成 18 条人工审核与 AI 复核，不能新建实验基线"
                        )
                    raise ConflictError(
                        f"{active_experiment.get('name', '当前实验')} 尚未生成完成的 V1 校验结果，不能新建实验基线"
                    )
        now = utc_now()
        count = await self._db.verification_experiments.count_documents({"job_id": job_id})
        sequence = count + 1
        verification_artifact_count = await self._verification_artifact_count(job_id)
        experiment = {
            "_id": f"experiment_{uuid4().hex}",
            "job_id": job_id,
            "name": f"实验 E{sequence}",
            "sequence": sequence,
            "status": "active",
            "matching_version_id": job.get("active_matching_version_id", "M0"),
            "artifact_count": verification_artifact_count,
            "source_snapshot": {
                "matching_version_id": job.get("active_matching_version_id", "M0"),
                "verification_artifact_count": verification_artifact_count,
            },
            "created_at": now,
        }
        await self._db.verification_experiments.update_many(
            {"job_id": job_id, "status": "active"},
            {"$set": {"status": "archived", "archived_at": now}},
        )
        await self._db.verification_experiments.insert_one(experiment)
        await self.update_job(job_id, active_verification_experiment_id=experiment["_id"])
        return experiment

    async def mark_stale_active_verification_runs(self) -> int:
        now = utc_now()
        result = await self._db.ai_verification_runs.update_many(
            {"status": {"$in": ["queued", "running"]}},
            {
                "$set": {
                    "status": "failed",
                    "error": "AI 复核在服务重启后中断，请重新点击完成核验",
                    "completed_at": now,
                    "updated_at": now,
                }
            },
        )
        await self._db.verification_sessions.update_many(
            {"status": "ai_review"},
            {
                "$set": {
                    "status": "in_progress",
                    "ai_run_id": None,
                    "updated_at": now,
                }
            },
        )
        machine_result = await self._db.machine_verification_runs.update_many(
            {"status": {"$in": ["queued", "running"]}},
            {
                "$set": {
                    "status": "failed",
                    "error": "全量机器校验在服务重启后中断，请重新执行校验",
                    "completed_at": now,
                    "updated_at": now,
                }
            },
        )
        return int(result.modified_count + machine_result.modified_count)

    async def create_machine_verification_run(
        self,
        *,
        job_id: str,
        rules: list[dict[str, Any]],
        sample_size: int,
        mode: str,
        target_version: int,
        calibration_profile: dict[str, Any] | None = None,
        assertion_baseline: dict[str, Any] | None = None,
        selected_rules: list[dict[str, Any]] | None = None,
        experiment: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        active = await self._db.machine_verification_runs.find_one(
            {"job_id": job_id, "status": {"$in": ["queued", "running"]}}
        )
        if active is not None:
            return active
        now = utc_now()
        run = {
            "_id": f"machine_verify_{uuid4().hex}",
            "job_id": job_id,
            "experiment_id": (experiment or {}).get("_id", "legacy"),
            "experiment_name": (experiment or {}).get("name", "历史校验"),
            "matching_version_id": (experiment or {}).get("matching_version_id", "M0"),
            "mode": mode,
            "target_version": target_version,
            "status": "queued",
            "pause_requested": False,
            "progress": {"current": 0, "total": 0, "percent": 0},
            "rules": rules,
            "calibration_profile": calibration_profile,
            "assertion_baseline": assertion_baseline or {"id": "v1", "name": "LLM 断言 V1"},
            "selected_rules": selected_rules or [],
            "evaluation_mode": "binary_fail_closed",
            "sample_size": sample_size,
            "total_artifacts": 0,
            "pass_count": 0,
            "fail_count": 0,
            "uncertain_count": 0,
            "model_unavailable_count": 0,
            "model_unavailable_reason": None,
            "session_id": None,
            "version_id": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        await self._db.machine_verification_runs.insert_one(run)
        return run

    async def get_machine_verification_run(
        self,
        *,
        job_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        run = await self._db.machine_verification_runs.find_one(
            {"_id": run_id, "job_id": job_id}
        )
        if run is None:
            raise NotFoundError("全量机器校验任务不存在")
        return run

    async def get_active_machine_verification_run(self, job_id: str) -> dict[str, Any] | None:
        return await self._db.machine_verification_runs.find_one(
            {"job_id": job_id, "status": {"$in": ["queued", "running", "paused"]}},
            sort=[("updated_at", DESCENDING)],
        )

    async def update_machine_verification_run(
        self,
        run_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        fields["updated_at"] = utc_now()
        run = await self._db.machine_verification_runs.find_one_and_update(
            {"_id": run_id},
            {"$set": fields},
            return_document=ReturnDocument.AFTER,
        )
        if run is None:
            raise NotFoundError("全量机器校验任务不存在")
        return run

    async def pause_machine_verification_run(
        self,
        *,
        job_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        run = await self._db.machine_verification_runs.find_one_and_update(
            {
                "_id": run_id,
                "job_id": job_id,
                "status": {"$in": ["queued", "running"]},
            },
            {
                "$set": {
                    "status": "paused",
                    "pause_requested": True,
                    "updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if run is not None:
            return run
        current = await self.get_machine_verification_run(job_id=job_id, run_id=run_id)
        if current.get("status") == "paused":
            return current
        raise ConflictError("当前全量机器校验任务无法暂停")

    async def resume_machine_verification_run(
        self,
        *,
        job_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        run = await self._db.machine_verification_runs.find_one_and_update(
            {"_id": run_id, "job_id": job_id, "status": "paused"},
            {
                "$set": {
                    "status": "queued",
                    "pause_requested": False,
                    "error": None,
                    "updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if run is None:
            raise ConflictError("当前全量机器校验任务无法继续")
        return run

    async def terminate_machine_verification_run(
        self,
        *,
        job_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Terminate an unfinished run and discard its partial machine decisions."""

        run = await self._db.machine_verification_runs.find_one_and_update(
            {
                "_id": run_id,
                "job_id": job_id,
                "status": {"$in": ["queued", "running", "paused"]},
            },
            {
                "$set": {
                    "status": "terminated",
                    "pause_requested": False,
                    "error": "用户已终止本次校验；可修改规则后重新执行",
                    "pass_count": 0,
                    "fail_count": 0,
                    "uncertain_count": 0,
                    "progress": {"current": 0, "total": 0, "percent": 0},
                    "completed_at": utc_now(),
                    "updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if run is not None:
            return run
        current = await self.get_machine_verification_run(job_id=job_id, run_id=run_id)
        if current.get("status") == "terminated":
            return current
        raise ConflictError("当前全量机器校验任务无法终止")

    async def clear_machine_verification_items(self, *, job_id: str, run_id: str) -> None:
        """Never reuse partial decisions after the user changes assertion rules."""

        await self._db.machine_verification_items.delete_many(
            {"job_id": job_id, "run_id": run_id}
        )

    async def reset_invalid_v1_experiment(self, job_id: str) -> bool:
        """Discard only a V1 that was invalidated by a systemic model outage.

        This deliberately keeps extracted records, OCR, relations, and the
        experiment itself.  It makes the next execution V1 again, rather than
        incorrectly promoting an unavailable-model result into V2.
        """

        experiment = await self.get_active_verification_experiment(job_id)
        experiment_id = experiment.get("_id", "legacy")
        version = await self._db.verification_versions.find_one(
            {
                "job_id": job_id,
                **self._experiment_filter(experiment_id),
                "version": 1,
            }
        )
        if version is None:
            # Idempotent by design: a prior request may have completed after the
            # browser lost its response.  Returning success lets the UI reload
            # the now-empty V1 state instead of leaving a stale button behind.
            return False
        later_version = await self._db.verification_versions.find_one(
            {
                "job_id": job_id,
                **self._experiment_filter(experiment_id),
                "version": {"$gt": 1},
            },
            projection={"_id": 1},
        )
        if later_version is not None:
            raise ConflictError("V1 后已存在后续断言版本，不能单独重置")

        run_id = version.get("machine_run_id")
        if not run_id:
            raise ConflictError("该 V1 不含机器校验记录，不能按模型故障重置")
        run = await self.get_machine_verification_run(job_id=job_id, run_id=run_id)
        machine_items = await self.list_machine_verification_items(run_id)
        unavailable_count = int(run.get("model_unavailable_count") or 0)
        if not unavailable_count:
            # Compatibility for runs created before the outage marker existed.
            unavailable_count = sum(
                item.get("model_unavailable") is True
                or "模型校验服务返回 HTTP 402" in str(item.get("reason", ""))
                or "Insufficient Balance" in str(item.get("reason", ""))
                for item in machine_items
            )
        total = int(run.get("total_artifacts") or len(machine_items))
        if not unavailable_count or not total or unavailable_count * 5 < total:
            raise ConflictError("当前 V1 不是由系统性模型服务异常产生，不能重置")

        sessions = await self._db.verification_sessions.find(
            {
                "job_id": job_id,
                "$and": [
                    self._experiment_filter(experiment_id),
                    {
                        "$or": [
                            {"machine_run_id": run_id},
                            {"version_id": version["_id"]},
                        ]
                    },
                ],
            }
        ).to_list(length=20)
        session_ids = [session["_id"] for session in sessions]
        if session_ids:
            await self._db.ai_verification_runs.delete_many(
                {"job_id": job_id, "session_id": {"$in": session_ids}}
            )
            await self._db.verification_sessions.delete_many(
                {"_id": {"$in": session_ids}, "job_id": job_id}
            )
        await self._db.verification_cohorts.delete_many(
            {"job_id": job_id, "machine_run_id": run_id, **self._experiment_filter(experiment_id)}
        )
        await self._db.verification_versions.delete_one({"_id": version["_id"], "job_id": job_id})
        await self._db.machine_verification_items.delete_many({"job_id": job_id, "run_id": run_id})
        await self._db.machine_verification_runs.delete_one({"_id": run_id, "job_id": job_id})
        return True

    async def replace_machine_verification_items(
        self,
        *,
        job_id: str,
        run_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        if not items:
            return
        operations = [
            UpdateOne(
                {"run_id": run_id, "record_id": item["record_id"]},
                {
                    "$set": {
                        **item,
                        "job_id": job_id,
                        "run_id": run_id,
                        "updated_at": utc_now(),
                    },
                    "$setOnInsert": {"created_at": utc_now()},
                },
                upsert=True,
            )
            for item in items
        ]
        await self._db.machine_verification_items.bulk_write(operations, ordered=False)

    async def list_machine_verification_items(self, run_id: str) -> list[dict[str, Any]]:
        cursor = self._db.machine_verification_items.find({"run_id": run_id})
        return await cursor.to_list(length=100000)

    async def create_initial_session_from_machine_run(
        self,
        *,
        job_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        run = await self.get_machine_verification_run(job_id=job_id, run_id=run_id)
        experiment_id = run.get("experiment_id", "legacy")
        existing = await self.get_active_verification_session(job_id, experiment_id=experiment_id)
        if existing is not None:
            return existing
        existing_cohort = await self._db.verification_cohorts.find_one(
            {"job_id": job_id, "machine_run_id": run_id, **self._experiment_filter(experiment_id)}
        )
        machine_items = await self.list_machine_verification_items(run_id)
        if existing_cohort is None:
            seed = secrets.randbelow(2**31 - 1)
            rng = random.Random(seed)
            passed = [item for item in machine_items if item.get("verdict") == "passed"]
            failed = [item for item in machine_items if item.get("verdict") == "failed"]
            uncertain = [item for item in machine_items if item.get("verdict") == "uncertain"]
            rng.shuffle(passed)
            rng.shuffle(failed)
            rng.shuffle(uncertain)
            pass_target = run.get("sample_size", 18) // 2
            fail_target = run.get("sample_size", 18) - pass_target
            selected = [*passed[:pass_target], *failed[:fail_target]]
            used = {str(item["record_id"]) for item in selected}
            for item in [*passed[pass_target:], *failed[fail_target:], *uncertain]:
                if len(selected) >= run.get("sample_size", 18):
                    break
                if str(item["record_id"]) not in used:
                    selected.append(item)
                    used.add(str(item["record_id"]))
            cohort = {
                "_id": f"cohort_{uuid4().hex}",
                "job_id": job_id,
                "experiment_id": experiment_id,
                "experiment_name": run.get("experiment_name", "历史校验"),
                "sample_size": len(selected),
                "random_seed": seed,
                "record_ids": [str(item["record_id"]) for item in selected],
                "sampling_strategy": "machine_verdict_balanced_v2",
                "eligible_count": len(machine_items),
                "correct_pool_size": len(passed),
                "incorrect_pool_size": len(failed),
                "strata_by_record": {
                    str(item["record_id"]): {
                        "machine_verdict": item.get("verdict"),
                        "machine_confidence": item.get("confidence"),
                    }
                    for item in selected
                },
                "machine_run_id": run_id,
                "created_at": utc_now(),
            }
            await self._db.verification_cohorts.insert_one(cohort)
        else:
            cohort = existing_cohort

        machine_by_record = {str(item["record_id"]): item for item in machine_items}
        records = await self._db.extraction_records.find(
            {"job_id": job_id, "_id": {"$in": cohort.get("record_ids", [])}}
        ).to_list(length=max(len(cohort.get("record_ids", [])), 1))
        record_by_id = {str(record["_id"]): record for record in records}
        items = []
        for record_id in cohort.get("record_ids", []):
            machine = machine_by_record.get(str(record_id), {})
            stale = str(record_id) not in record_by_id
            items.append(
                {
                    "record_id": str(record_id),
                    "verdict": "stale" if stale else "unreviewed",
                    "failure_code": None,
                    "failure_reason": "固定样本已失效" if stale else "",
                    "relation_signature": self._record_relation_signature(
                        record_by_id.get(str(record_id), {})
                    ),
                    "relation_changed": False,
                    "sampling_strata": [f"machine:{machine.get('verdict', 'uncertain')}"],
                    "expected_label": None,
                    "stale": stale,
                    "reviewed_at": None,
                    "ai_verdict": None,
                    "ai_confidence": None,
                    "ai_reason": "",
                    "ai_field_results": [],
                    "machine_verdict": machine.get("verdict"),
                    "machine_confidence": machine.get("confidence"),
                    "machine_reason": str(machine.get("reason", ""))[:1000],
                    "gold_record_id": None,
                    "gold_match_status": None,
                    "consensus_status": "pending",
                    "conflict_resolved": False,
                }
            )
        session = {
            "_id": f"verify_{uuid4().hex}",
            "job_id": job_id,
            "experiment_id": experiment_id,
            "experiment_name": run.get("experiment_name", "历史校验"),
            "cohort_id": cohort["_id"],
            "target_version": run["target_version"],
            "status": "in_progress",
            "matching_version_id": run.get("matching_version_id", "M0"),
            "rules": run.get("rules", []),
            "assertion_baseline": run.get(
                "assertion_baseline", {"id": "v1", "name": "LLM 断言 V1"}
            ),
            "selected_rules": run.get("selected_rules", []),
            "items": items,
            "reviewed_count": 0,
            "sample_count": len(items),
            "version_id": None,
            "machine_run_id": run_id,
            "ai_run_id": None,
            "gold_dataset_id": None,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "completed_at": None,
        }
        await self._db.verification_sessions.insert_one(session)
        await self.update_machine_verification_run(run_id, session_id=session["_id"])
        return session

    @staticmethod
    def _build_calibrated_report(
        *,
        items: list[dict[str, Any]],
        total_artifacts: int,
        machine_run: dict[str, Any] | None,
    ) -> dict[str, Any]:
        pass_count = sum(item.get("verdict") == "passed" for item in items)
        fail_count = sum(item.get("verdict") == "failed" for item in items)
        stale_count = sum(bool(item.get("stale")) for item in items)
        changed_count = sum(bool(item.get("relation_changed")) for item in items)
        true_positive = sum(
            item.get("verdict") == "failed" and item.get("machine_verdict") == "failed"
            for item in items
        )
        true_negative = sum(
            item.get("verdict") == "passed" and item.get("machine_verdict") == "passed"
            for item in items
        )
        false_positive = sum(
            item.get("verdict") == "passed" and item.get("machine_verdict") == "failed"
            for item in items
        )
        false_negative = sum(
            item.get("verdict") == "failed" and item.get("machine_verdict") == "passed"
            for item in items
        )
        compared = true_positive + true_negative + false_positive + false_negative
        actual_errors = true_positive + false_negative
        predicted_errors = true_positive + false_positive
        return {
            "sample_count": len(items),
            "reviewed_count": pass_count + fail_count,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "stale_count": stale_count,
            "relation_changed_count": changed_count,
            "pass_rate": round(pass_count / (pass_count + fail_count), 4)
            if pass_count + fail_count
            else 0.0,
            "total_artifacts": total_artifacts,
            "ai_pass_count": sum(item.get("ai_verdict") == "passed" for item in items),
            "ai_fail_count": sum(item.get("ai_verdict") == "failed" for item in items),
            "ai_uncertain_count": sum(item.get("ai_verdict") == "uncertain" for item in items),
            "conflict_count": sum(
                item.get("consensus_status") in {"conflict", "human_resolved"}
                for item in items
            ),
            "benchmark_matched_count": sum(
                item.get("gold_match_status") == "matched" for item in items
            ),
            "full_pass_count": (machine_run or {}).get("pass_count", 0),
            "full_fail_count": (machine_run or {}).get("fail_count", 0),
            "full_uncertain_count": (machine_run or {}).get("uncertain_count", 0),
            "model_unavailable_count": (machine_run or {}).get("model_unavailable_count", 0),
            "model_unavailable_reason": (machine_run or {}).get("model_unavailable_reason"),
            "error_coverage": round(true_positive / actual_errors, 4) if actual_errors else None,
            "error_precision": round(true_positive / predicted_errors, 4)
            if predicted_errors
            else None,
            "human_machine_alignment": round((true_positive + true_negative) / compared, 4)
            if compared
            else None,
            "review_load": round(predicted_errors / compared, 4) if compared else None,
            "false_positive_rate": round(false_positive / (true_negative + false_positive), 4)
            if true_negative + false_positive
            else None,
            "false_negative_rate": round(false_negative / actual_errors, 4)
            if actual_errors
            else None,
            "true_positive_count": true_positive,
            "true_negative_count": true_negative,
            "false_positive_count": false_positive,
            "false_negative_count": false_negative,
        }

    @staticmethod
    def _field_error_distribution(machine_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Aggregate only explicit causes of final full-run failures.

        The former implementation counted every failed heuristic across every
        card and classified it using a substring of its display name. That made a
        passed card inflate a similarly named error bucket.
        """

        counts: dict[str, int] = {}
        seen: set[tuple[str, str]] = set()
        for item in machine_items:
            if item.get("verdict") != "failed":
                continue
            record_id = str(item.get("record_id", ""))
            has_failed_field = False
            for result in item.get("field_results", []):
                if not isinstance(result, dict) or result.get("verdict") != "failed":
                    continue
                if not result.get("causes_overall_failure", False):
                    continue
                has_failed_field = True
                key = VerificationPersistence._canonical_failure_code(result)
                marker = (record_id, key)
                if marker not in seen:
                    counts[key] = counts.get(key, 0) + 1
                    seen.add(marker)
            if not has_failed_field:
                marker = (record_id, "unclassified_failure")
                if marker not in seen:
                    counts["unclassified_failure"] = counts.get("unclassified_failure", 0) + 1
                    seen.add(marker)
        return [
            {"key": key, "count": count}
            for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]

    @staticmethod
    def _canonical_failure_code(result: dict[str, Any]) -> str:
        """Return a stable, explainable code without fuzzy label matching."""
        allowed = {
            "artifact_id_missing",
            "artifact_id_duplicate",
            "artifact_id_evidence_conflict",
            "sequence_crop_conflict",
            "figure_caption_missing",
            "figure_caption_evidence_conflict",
            "color_plate_relation_conflict",
            "artifact_crop_missing",
            "text_evidence_conflict",
            "structured_measurements_error",
            "structured_classification_error",
            "structured_field_evidence_conflict",
            "unclassified_failure",
        }
        code = str(result.get("failure_code", "")).strip()
        if code in allowed:
            return code

        # Compatibility for deterministic checks created during a rolling
        # deployment. Arbitrary model prose and partial substring matching are
        # deliberately not used as an error category.
        field = str(result.get("field", "")).strip().lower()
        reason = str(result.get("reason", ""))
        exact_map = {
            "artifact_crop": "artifact_crop_missing",
            "sequence_crop_relation": "sequence_crop_conflict",
            "figure": "figure_caption_missing",
            "figure_caption": "figure_caption_evidence_conflict",
            "measurements": "structured_measurements_error",
            "classification": "structured_classification_error",
            "text_evidence": "text_evidence_conflict",
            "color_plate_relation": "color_plate_relation_conflict",
        }
        if field == "identifier":
            return "artifact_id_duplicate" if "重复" in reason else "artifact_id_missing"
        return exact_map.get(field, "unclassified_failure")

    @staticmethod
    def _review_required_count(machine_items: list[dict[str, Any]]) -> int:
        """Keep non-blocking doubts visible without reporting them as failures."""
        return sum(
            1
            for item in machine_items
            if item.get("verdict") == "passed"
            and any(
                isinstance(result, dict) and result.get("verdict") in {"failed", "uncertain"}
                for result in item.get("field_results", [])
            )
        )

    async def finalize_calibrated_verification_session(
        self,
        *,
        job_id: str,
        session_id: str,
        run_id: str,
        calibration_profile: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Freeze a version using the post-human full rerun as the final result."""

        session = await self.get_verification_session(job_id=job_id, session_id=session_id)
        run = await self.get_machine_verification_run(job_id=job_id, run_id=run_id)
        machine_items = await self.list_machine_verification_items(run_id)
        machine_by_record = {str(item["record_id"]): item for item in machine_items}
        items = []
        for item in session.get("items", []):
            calibrated = machine_by_record.get(str(item.get("record_id")), {})
            items.append(
                {
                    **item,
                    "calibrated_machine_verdict": calibrated.get("verdict"),
                    "calibrated_machine_confidence": calibrated.get("confidence"),
                    "calibrated_machine_reason": str(calibrated.get("reason", ""))[:1000],
                }
            )
        total_artifacts = int(run.get("total_artifacts") or 0)
        if total_artifacts <= 0:
            total_artifacts = await self._db.extraction_records.count_documents({"job_id": job_id})
        report = self._build_calibrated_report(
            items=items,
            total_artifacts=total_artifacts,
            machine_run=run,
        )
        report["field_error_distribution"] = self._field_error_distribution(machine_items)
        report["review_required_count"] = self._review_required_count(machine_items)
        latest = await self._db.verification_versions.find_one(
            {"job_id": job_id}, sort=[("version", DESCENDING)]
        )
        now = utc_now()
        version = {
            "_id": f"version_{uuid4().hex}",
            "job_id": job_id,
            "cohort_id": session["cohort_id"],
            "version": session["target_version"],
            "parent_version_id": latest["_id"] if latest else None,
            "matching_version_id": session.get("matching_version_id", "M0"),
            "rules": session.get("rules", []),
            "items": items,
            "report": report,
            "machine_run_id": run_id,
            "initial_machine_run_id": session.get("machine_run_id"),
            "ai_run_id": session.get("ai_run_id"),
            "calibration_profile": calibration_profile,
            "gold_dataset_id": session.get("gold_dataset_id"),
            "gold_dataset_version": None,
            "created_at": now,
        }
        await self._db.verification_versions.insert_one(version)
        completed = await self._db.verification_sessions.find_one_and_update(
            {"_id": session_id, "job_id": job_id, "status": "ai_review"},
            {
                "$set": {
                    "status": "completed",
                    "items": items,
                    "version_id": version["_id"],
                    "calibration_profile": calibration_profile,
                    "reviewed_count": report["reviewed_count"],
                    "updated_at": now,
                    "completed_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if completed is None:
            await self._db.verification_versions.delete_one({"_id": version["_id"]})
            raise ConflictError("校验会话状态已经发生变化，请刷新后重试")
        await self.update_machine_verification_run(
            run_id,
            version_id=version["_id"],
            status="completed",
            completed_at=now,
        )
        return completed, version

    async def finalize_recheck_from_machine_run(
        self,
        *,
        job_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        run = await self.get_machine_verification_run(job_id=job_id, run_id=run_id)
        experiment_id = run.get("experiment_id", "legacy")
        latest = await self._db.verification_versions.find_one(
            {"job_id": job_id, **self._experiment_filter(experiment_id)},
            sort=[("version", DESCENDING)],
        )
        if latest is None:
            raise ConflictError("尚未完成人工校准，不能直接复用样本执行断言")
        machine_by_record = {
            str(item["record_id"]): item
            for item in await self.list_machine_verification_items(run_id)
        }
        items: list[dict[str, Any]] = []
        for previous in latest.get("items", []):
            machine = machine_by_record.get(str(previous.get("record_id")), {})
            items.append(
                {
                    **previous,
                    "machine_verdict": machine.get("verdict"),
                    "machine_confidence": machine.get("confidence"),
                    "machine_reason": str(machine.get("reason", ""))[:1000],
                    "ai_verdict": None,
                    "ai_confidence": None,
                    "ai_reason": "",
                    "ai_field_results": [],
                    "consensus_status": "pending",
                }
            )
        total_artifacts = int(run.get("total_artifacts") or 0)
        if total_artifacts <= 0:
            total_artifacts = await self._db.extraction_records.count_documents({"job_id": job_id})
        machine_items = await self.list_machine_verification_items(run_id)
        report = self._build_calibrated_report(
            items=items,
            total_artifacts=total_artifacts,
            machine_run=run,
        )
        report["field_error_distribution"] = self._field_error_distribution(machine_items)
        report["review_required_count"] = self._review_required_count(machine_items)
        now = utc_now()
        version = {
            "_id": f"version_{uuid4().hex}",
            "job_id": job_id,
            "experiment_id": experiment_id,
            "experiment_name": run.get("experiment_name", "历史校验"),
            "cohort_id": latest["cohort_id"],
            "version": run["target_version"],
            "parent_version_id": latest["_id"],
            "matching_version_id": latest.get("matching_version_id", "M0"),
            "rules": run.get("rules", []),
            "assertion_baseline": run.get(
                "assertion_baseline", {"id": "v1", "name": "LLM 断言 V1"}
            ),
            "selected_rules": run.get("selected_rules", []),
            "items": items,
            "report": report,
            "machine_run_id": run_id,
            "ai_run_id": None,
            "gold_dataset_id": latest.get("gold_dataset_id"),
            "gold_dataset_version": latest.get("gold_dataset_version"),
            "calibration_profile": latest.get("calibration_profile"),
            "created_at": now,
        }
        await self._db.verification_versions.insert_one(version)
        await self.update_machine_verification_run(
            run_id,
            status="completed",
            version_id=version["_id"],
            completed_at=now,
            progress={
                "current": run.get("total_artifacts", 0),
                "total": run.get("total_artifacts", 0),
                "percent": 100,
            },
        )
        return version


    async def get_or_create_verification_cohort(
        self,
        *,
        job_id: str,
        sample_size: int,
        rules: list[dict[str, Any]],
    ) -> dict[str, Any]:
        existing = await self._db.verification_cohorts.find_one({"job_id": job_id})
        if existing is not None:
            if existing.get("sampling_strategy") == "balanced_correct_incorrect_v1":
                return existing
            await self._db.verification_cohorts.delete_one({"_id": existing["_id"]})

        records = (
            await self._db.extraction_records.find({"job_id": job_id})
            .sort([("source_pages", 1), ("created_at", 1)])
            .to_list(length=100000)
        )
        if not records:
            raise DomainError("当前抽取任务没有可核验的器物记录")

        seed = secrets.randbelow(2**31 - 1)
        relations = await self.list_job_relations(job_id)
        regions = await self.list_job_regions(job_id)
        selection = select_balanced_verification_sample(
            records=records,
            relations=relations,
            regions=regions,
            rules=rules,
            sample_size=sample_size,
            seed=seed,
        )
        now = utc_now()
        cohort = {
            "_id": f"cohort_{uuid4().hex}",
            "job_id": job_id,
            "sample_size": len(selection.record_ids),
            "random_seed": seed,
            "record_ids": selection.record_ids,
            "sampling_strategy": "balanced_correct_incorrect_v1",
            "eligible_count": selection.eligible_count,
            "correct_pool_size": selection.correct_pool_size,
            "incorrect_pool_size": selection.incorrect_pool_size,
            "strata_by_record": selection.metadata,
            "created_at": now,
        }
        try:
            await self._db.verification_cohorts.insert_one(cohort)
            return cohort
        except DuplicateKeyError:
            concurrent = await self._db.verification_cohorts.find_one({"job_id": job_id})
            if concurrent is None:
                raise
            return concurrent


    async def create_verification_session(
        self,
        *,
        job_id: str,
        rules: list[dict[str, Any]],
        sample_size: int,
    ) -> dict[str, Any]:
        job = await self.get_job(job_id)
        if job.get("status") not in {"completed", "completed_with_warnings"}:
            raise ConflictError("抽取任务完成后才能执行校验")

        active = await self.get_active_verification_session(job_id)
        if active is not None:
            if active.get("status") == "ai_review":
                active = await self.reopen_verification_session_for_human_review(
                    job_id=job_id,
                    session_id=active["_id"],
                )
            return active

        cohort = await self.get_or_create_verification_cohort(
            job_id=job_id,
            sample_size=sample_size,
            rules=rules,
        )
        cohort_records = await self._db.extraction_records.find(
            {"job_id": job_id, "_id": {"$in": cohort["record_ids"]}}
        ).to_list(length=max(len(cohort["record_ids"]), 1))
        record_by_id = {str(record["_id"]): record for record in cohort_records}
        latest = await self._db.verification_versions.find_one(
            {"job_id": job_id},
            sort=[("version", DESCENDING)],
        )
        target_version = int(latest.get("version", 0)) + 1 if latest else 1
        previous_item_by_id = {
            str(item.get("record_id")): item for item in (latest or {}).get("items", [])
        }
        strata_by_record = cohort.get("strata_by_record", {})

        def build_item(record_id: str) -> dict[str, Any]:
            record = record_by_id.get(str(record_id))
            relation_signature = self._record_relation_signature(record or {})
            previous_signature = str(
                previous_item_by_id.get(str(record_id), {}).get("relation_signature", "")
            )
            stale = record is None
            strata = strata_by_record.get(str(record_id), {})
            expected_label = strata.get("expected_label")
            sampling_strata = list(strata.get("rule_states", []))
            if expected_label:
                sampling_strata = [f"expected:{expected_label}", *sampling_strata]
            return {
                "record_id": record_id,
                "verdict": "stale" if stale else "unreviewed",
                "failure_code": None,
                "failure_reason": "固定样本在当前匹配版本中已失效" if stale else "",
                "relation_signature": relation_signature,
                "relation_changed": bool(
                    not stale
                    and previous_signature
                    and relation_signature != previous_signature
                ),
                "sampling_strata": sampling_strata,
                "expected_label": expected_label,
                "stale": stale,
                "reviewed_at": None,
                "ai_verdict": None,
                "ai_confidence": None,
                "ai_reason": "",
                "ai_field_results": [],
                "gold_record_id": None,
                "gold_match_status": None,
                "consensus_status": "pending",
                "conflict_resolved": False,
            }

        items = [build_item(record_id) for record_id in cohort["record_ids"]]
        now = utc_now()
        session = {
            "_id": f"verify_{uuid4().hex}",
            "job_id": job_id,
            "cohort_id": cohort["_id"],
            "target_version": target_version,
            "status": "in_progress",
            "matching_version_id": job.get("active_matching_version_id", "M0"),
            "rules": rules,
            "items": items,
            "reviewed_count": sum(item["verdict"] != "unreviewed" for item in items),
            "sample_count": len(cohort["record_ids"]),
            "version_id": None,
            "ai_run_id": None,
            "gold_dataset_id": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        try:
            await self._db.verification_sessions.insert_one(session)
        except DuplicateKeyError:
            concurrent = await self.get_active_verification_session(job_id)
            if concurrent is None:
                raise
            return concurrent
        return session


    async def reopen_verification_session_for_human_review(
        self,
        *,
        job_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        updated = await self._db.verification_sessions.find_one_and_update(
            {"_id": session_id, "job_id": job_id, "status": "ai_review"},
            {
                "$set": {
                    "status": "in_progress",
                    "ai_run_id": None,
                    "updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is not None:
            return updated
        return await self.get_verification_session(job_id=job_id, session_id=session_id)


    async def get_active_verification_session(
        self,
        job_id: str,
        *,
        experiment_id: str | None = None,
    ) -> dict[str, Any] | None:
        return await self._db.verification_sessions.find_one(
            {
                "job_id": job_id,
                **self._experiment_filter(experiment_id),
                "status": {"$in": ["in_progress", "ai_review", "conflict_review"]},
            }
        )


    async def get_verification_session(
        self,
        *,
        job_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        session = await self._db.verification_sessions.find_one(
            {"_id": session_id, "job_id": job_id}
        )
        if session is None:
            raise NotFoundError("校验会话不存在")
        return session


    async def list_verification_session_records(
        self,
        *,
        job_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        session = await self.get_verification_session(job_id=job_id, session_id=session_id)
        record_ids = [item["record_id"] for item in session.get("items", [])]
        cursor = self._db.extraction_records.find({"job_id": job_id, "_id": {"$in": record_ids}})
        records = await cursor.to_list(length=max(len(record_ids), 1))
        by_id = {record["_id"]: record for record in records}
        selected = [by_id[record_id] for record_id in record_ids if record_id in by_id]

        # The machine validator intentionally prefers the richest text record
        # for an entity.  That record can be a different page-level record than
        # the one that owns the detected artifact crop.  The review catalogue
        # must still render all fixed samples, so hydrate only the presentation
        # anchor from its sibling while retaining the reviewed record ID.
        entity_ids = {
            str(record["entity_id"])
            for record in selected
            if record.get("entity_id")
            and not (record.get("primary_artifact_region_id") or record.get("thumbnail_region_id"))
        }
        if not entity_ids:
            return selected
        siblings = await self._db.extraction_records.find(
            {
                "job_id": job_id,
                "entity_id": {"$in": list(entity_ids)},
                "$or": [
                    {"primary_artifact_region_id": {"$ne": None}},
                    {"thumbnail_region_id": {"$ne": None}},
                ],
            },
            projection={"entity_id": 1, "primary_artifact_region_id": 1, "thumbnail_region_id": 1},
        ).to_list(length=max(len(entity_ids) * 10, 10))
        anchor_by_entity: dict[str, dict[str, Any]] = {}
        for sibling in siblings:
            entity_id = str(sibling.get("entity_id") or "")
            if not entity_id or entity_id in anchor_by_entity:
                continue
            if sibling.get("primary_artifact_region_id") or sibling.get("thumbnail_region_id"):
                anchor_by_entity[entity_id] = sibling

        return [
            {
                **record,
                "primary_artifact_region_id": anchor.get("primary_artifact_region_id"),
                "thumbnail_region_id": anchor.get("thumbnail_region_id"),
                # Helpful provenance for clients without changing the reviewed
                # record or the database source of truth.
                "review_display_crop_record_id": anchor.get("_id"),
            }
            if not (record.get("primary_artifact_region_id") or record.get("thumbnail_region_id"))
            and (anchor := anchor_by_entity.get(str(record.get("entity_id") or "")))
            else record
            for record in selected
        ]


    async def update_verification_item(
        self,
        *,
        job_id: str,
        session_id: str,
        record_id: str,
        verdict: str,
        failure_code: str | None,
        failure_reason: str,
    ) -> dict[str, Any]:
        session = await self.get_verification_session(job_id=job_id, session_id=session_id)
        if session.get("status") not in {"in_progress", "conflict_review"}:
            raise ConflictError("已完成的校验版本不能修改")
        items = session.get("items", [])
        if not any(item.get("record_id") == record_id for item in items):
            raise NotFoundError("该器物不属于当前固定样本集")
        if any(
            item.get("record_id") == record_id and item.get("stale")
            for item in items
        ):
            raise ConflictError("该固定样本在当前匹配版本中已失效，不能提交核验结果")

        now = utc_now()
        resolving_conflict = session.get("status") == "conflict_review"
        updated_items = [
            {
                **item,
                "verdict": verdict,
                "failure_code": failure_code if verdict == "failed" else None,
                "failure_reason": failure_reason if verdict == "failed" else "",
                "reviewed_at": now,
                "conflict_resolved": bool(
                    resolving_conflict and item.get("consensus_status") == "conflict"
                ),
                "consensus_status": (
                    "human_resolved"
                    if resolving_conflict and item.get("consensus_status") == "conflict"
                    else item.get("consensus_status", "pending")
                ),
            }
            if item.get("record_id") == record_id
            else item
            for item in items
        ]
        reviewed_count = sum(item.get("verdict") != "unreviewed" for item in updated_items)
        updated = await self._db.verification_sessions.find_one_and_update(
            {
                "_id": session_id,
                "job_id": job_id,
                "status": {"$in": ["in_progress", "conflict_review"]},
            },
            {
                "$set": {
                    "items": updated_items,
                    "reviewed_count": reviewed_count,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise ConflictError("校验会话状态已经发生变化，请刷新后重试")
        return updated


    async def create_ai_verification_run(
        self,
        *,
        job_id: str,
        session_id: str,
        gold_dataset_id: str | None,
        total: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        session = await self.get_verification_session(job_id=job_id, session_id=session_id)
        if session.get("status") != "in_progress":
            raise ConflictError("当前校验会话不能启动 AI 复核")
        now = utc_now()
        run = {
            "_id": f"airun_{uuid4().hex}",
            "job_id": job_id,
            "session_id": session_id,
            "status": "queued",
            "progress": {"current": 0, "total": total, "percent": 0},
            "gold_dataset_id": gold_dataset_id,
            "benchmark_available": bool(gold_dataset_id),
            "conflict_count": 0,
            "uncertain_count": 0,
            "version_id": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        await self._db.ai_verification_runs.insert_one(run)
        updated = await self._db.verification_sessions.find_one_and_update(
            {"_id": session_id, "job_id": job_id, "status": "in_progress"},
            {
                "$set": {
                    "status": "ai_review",
                    "ai_run_id": run["_id"],
                    "gold_dataset_id": gold_dataset_id,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            await self._db.ai_verification_runs.delete_one({"_id": run["_id"]})
            raise ConflictError("校验会话状态已发生变化，请刷新后重试")
        return updated, run


    async def get_ai_verification_run(
        self,
        *,
        job_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        run = await self._db.ai_verification_runs.find_one({"_id": run_id, "job_id": job_id})
        if run is None:
            raise NotFoundError("AI 复核任务不存在")
        return run


    async def update_ai_verification_run(self, run_id: str, **fields: Any) -> dict[str, Any]:
        fields["updated_at"] = utc_now()
        run = await self._db.ai_verification_runs.find_one_and_update(
            {"_id": run_id},
            {"$set": fields},
            return_document=ReturnDocument.AFTER,
        )
        if run is None:
            raise NotFoundError("AI 复核任务不存在")
        return run


    async def apply_ai_verification_results(
        self,
        *,
        job_id: str,
        session_id: str,
        run_id: str,
        results: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        session = await self.get_verification_session(job_id=job_id, session_id=session_id)
        if session.get("status") != "ai_review" or session.get("ai_run_id") != run_id:
            raise ConflictError("AI 复核结果对应的会话已发生变化")
        items: list[dict[str, Any]] = []
        now = utc_now()
        for item in session.get("items", []):
            result = results.get(str(item.get("record_id")))
            if not result:
                items.append(item)
                continue
            human_verdict = item.get("verdict")
            human_reviewed_at = item.get("reviewed_at")
            human_failure_code = item.get("failure_code")
            human_failure_reason = item.get("failure_reason")
            merged = {**item, **result}
            merged["verdict"] = human_verdict
            merged["reviewed_at"] = human_reviewed_at
            merged["failure_code"] = human_failure_code
            merged["failure_reason"] = human_failure_reason
            items.append(merged)
        updated = await self._db.verification_sessions.find_one_and_update(
            {"_id": session_id, "job_id": job_id, "status": "ai_review", "ai_run_id": run_id},
            {
                "$set": {
                    "items": items,
                    # Keep ai_review until finalize freezes the version. Conflicts are
                    # informational only and no longer route users into conflict_review.
                    "status": "ai_review",
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise ConflictError("AI 复核结果保存失败，请刷新后重试")
        return updated


    async def reset_failed_ai_verification(
        self,
        *,
        job_id: str,
        session_id: str,
        run_id: str,
    ) -> None:
        await self._db.verification_sessions.update_one(
            {"_id": session_id, "job_id": job_id, "status": "ai_review", "ai_run_id": run_id},
            {"$set": {"status": "in_progress", "ai_run_id": None, "updated_at": utc_now()}},
        )


    async def finalize_verification_session(
        self,
        *,
        job_id: str,
        session_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        session = await self.get_verification_session(job_id=job_id, session_id=session_id)
        if session.get("status") == "completed" and session.get("version_id"):
            version = await self._db.verification_versions.find_one(
                {"_id": session["version_id"], "job_id": job_id}
            )
            if version is not None:
                return session, version

        items = session.get("items", [])
        unreviewed = [item for item in items if item.get("verdict") == "unreviewed"]
        if unreviewed:
            raise ConflictError(f"还有 {len(unreviewed)} 条样本尚未完成核验")

        machine_run = None
        if session.get("machine_run_id"):
            machine_run = await self.get_machine_verification_run(
                job_id=job_id,
                run_id=session["machine_run_id"],
            )
        total_artifacts = int((machine_run or {}).get("total_artifacts") or 0)
        if total_artifacts <= 0:
            total_artifacts = await self._db.extraction_records.count_documents({"job_id": job_id})
        report = self._build_calibrated_report(
            items=items,
            total_artifacts=total_artifacts,
            machine_run=machine_run,
        )
        # V1 uses the first full machine run, while V2+ already aggregates its
        # recheck run below.  Keep their error-field distribution comparable.
        if machine_run is not None:
            machine_items = await self.list_machine_verification_items(machine_run["_id"])
            report["field_error_distribution"] = self._field_error_distribution(machine_items)
            report["review_required_count"] = self._review_required_count(machine_items)
        experiment_id = session.get("experiment_id", "legacy")
        latest = await self._db.verification_versions.find_one(
            {"job_id": job_id, **self._experiment_filter(experiment_id)},
            sort=[("version", DESCENDING)],
        )
        parent_version_id = latest["_id"] if latest else None
        now = utc_now()
        version = {
            "_id": f"version_{uuid4().hex}",
            "job_id": job_id,
            "experiment_id": experiment_id,
            "experiment_name": session.get("experiment_name", "历史校验"),
            "cohort_id": session["cohort_id"],
            "version": session["target_version"],
            "parent_version_id": parent_version_id,
            "matching_version_id": session.get("matching_version_id", "M0"),
            "rules": session.get("rules", []),
            "assertion_baseline": session.get(
                "assertion_baseline", {"id": "v1", "name": "LLM 断言 V1"}
            ),
            "selected_rules": session.get("selected_rules", []),
            "items": items,
            "report": report,
            "machine_run_id": session.get("machine_run_id"),
            "ai_run_id": session.get("ai_run_id"),
            "gold_dataset_id": session.get("gold_dataset_id"),
            "gold_dataset_version": None,
            "created_at": now,
        }
        if session.get("gold_dataset_id"):
            dataset = await self._db.gold_datasets.find_one({"_id": session["gold_dataset_id"]})
            version["gold_dataset_version"] = (dataset or {}).get("version")
        try:
            await self._db.verification_versions.insert_one(version)
        except DuplicateKeyError as exc:
            raise ConflictError("该校验版本已经存在，请刷新后重试") from exc

        completed = await self._db.verification_sessions.find_one_and_update(
            {
                "_id": session_id,
                "job_id": job_id,
                "status": {"$in": ["in_progress", "ai_review", "conflict_review"]},
            },
            {
                "$set": {
                    "status": "completed",
                    "version_id": version["_id"],
                    "reviewed_count": report["reviewed_count"],
                    "updated_at": now,
                    "completed_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if completed is None:
            await self._db.verification_versions.delete_one({"_id": version["_id"]})
            raise ConflictError("校验会话状态已经发生变化，请刷新后重试")
        return completed, version


    async def list_verification_versions(
        self,
        job_id: str,
        *,
        experiment_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if experiment_id is None:
            experiment = await self.get_active_verification_experiment(job_id)
            experiment_id = experiment["_id"]
        cursor = self._db.verification_versions.find(
            {"job_id": job_id, **self._experiment_filter(experiment_id)}
        ).sort("version", DESCENDING)
        return await cursor.to_list(length=1000)


    async def get_verification_version(
        self,
        *,
        job_id: str,
        version_id: str,
    ) -> dict[str, Any]:
        version = await self._db.verification_versions.find_one(
            {"_id": version_id, "job_id": job_id}
        )
        if version is None:
            raise NotFoundError("校验版本不存在")
        return version

    async def get_machine_verification_export_data(
        self,
        *,
        job_id: str,
        version_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
        """Return one immutable version together with the full machine-run detail."""

        version = await self.get_verification_version(job_id=job_id, version_id=version_id)
        run_id = version.get("machine_run_id")
        if not run_id:
            raise ConflictError("该校验版本没有对应的全量机器校验结果，无法导出明细")
        machine_run = await self.get_machine_verification_run(job_id=job_id, run_id=run_id)
        machine_items = await self.list_machine_verification_items(run_id)
        record_ids = [str(item.get("record_id")) for item in machine_items if item.get("record_id")]
        records = await self._db.extraction_records.find(
            {"job_id": job_id, "_id": {"$in": record_ids}}
        ).to_list(length=max(len(record_ids), 1))
        return version, machine_run, machine_items, records

