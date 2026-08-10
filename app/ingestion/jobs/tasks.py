"""Celery task layer: scheduling, retries, rate limiting, and per-source
failure isolation around the ingestion pipeline (app.ingestion.pipeline).

Design invariant: a failure in one source's task must never affect another
source's task -- each source gets its own IngestionJob row and its own
Celery task invocation, so a broken adapter or an unreachable source degrades
that one source only.
"""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select

from app.compliance.source_policy import CollectionNotPermittedError, SourcePolicy, rate_limiter
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.ingestion.jobs.celery_app import celery_app
from app.ingestion.pipeline import ingest_parsed_record
from app.models.ingestion_job import IngestionJob
from app.models.source import Source
from app.source_adapters.base import SourceAdapter

logger = get_logger(__name__)


def _adapter_for(source: Source) -> SourceAdapter:
    if source.source_type == "website":
        from app.source_adapters.website_adapter import WebsiteAdapter

        return WebsiteAdapter(source_name=source.name)
    if source.source_type == "government_dataset":
        from app.source_adapters.government_dataset_adapter import GovernmentDatasetAdapter

        return GovernmentDatasetAdapter(source_name=source.name)
    if source.source_type == "registry_data_provider":
        from app.source_adapters.filesure_adapter import FileSureAdapter

        return FileSureAdapter(source_name=source.name)
    if source.source_type == "user_file":
        from app.source_adapters.custom_file_adapter import CustomFileAdapter

        field_mapping = (source.metadata_json or {}).get("field_mapping") or {}
        return CustomFileAdapter(source_name=source.name, field_mapping=field_mapping)
    raise ValueError(f"No adapter registered for source_type={source.source_type!r}")


def _policy_from_source(source: Source) -> SourcePolicy:
    return SourcePolicy(
        source_name=source.name,
        collection_enabled=source.collection_enabled,
        rate_limit_per_minute=source.rate_limit_per_minute,
        max_concurrency=source.max_concurrency,
        license_notes=source.license_notes or "",
        robots_notes=source.robots_notes or "",
    )


@celery_app.task
def dispatch_enabled_source_collections() -> dict:
    """Celery Beat entry point: fan out one run_source_collection task per
    enabled source. idempotency_key is the current date, so re-dispatching
    (e.g. a Beat misfire) never double-collects a source on the same day."""
    db = SessionLocal()
    try:
        sources = list(db.scalars(select(Source).where(Source.collection_enabled.is_(True))))
        idempotency_key = date.today().isoformat()
        dispatched = []
        for source in sources:
            run_source_collection.delay(str(source.id), None, idempotency_key)
            dispatched.append(source.name)
        return {"dispatched_sources": dispatched}
    finally:
        db.close()


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def run_source_collection(self, source_id: str, source_target: str | None, idempotency_key: str) -> dict:
    """Collect + ingest one source. `source_target` is the URL/resource id to
    fetch; production dispatch should supply the source's real, ToS-reviewed
    target here (or extend Source with a target-list column) -- there is no
    default target for any adapter registered in this codebase.
    """
    db = SessionLocal()
    try:
        source = db.get(Source, uuid.UUID(source_id))
        if source is None:
            return {"status": "skipped", "reason": "source_not_found"}

        existing = db.scalar(
            select(IngestionJob).where(
                IngestionJob.source_id == source.id, IngestionJob.idempotency_key == idempotency_key
            )
        )
        if existing and existing.status == "success":
            return {"status": "skipped", "reason": "already_completed", "job_id": str(existing.id)}

        job = existing or IngestionJob(source_id=source.id, idempotency_key=idempotency_key, status="pending")
        if existing is None:
            db.add(job)
            db.flush()

        policy = _policy_from_source(source)
        try:
            policy.assert_collection_allowed()
        except CollectionNotPermittedError as exc:
            job.status = "failed"
            job.error_summary = str(exc)
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(
                "source_collection_skipped_disabled",
                extra={"extra_fields": {"source": source.name, "job_id": str(job.id)}},
            )
            return {"status": "skipped", "reason": "collection_disabled"}

        if not rate_limiter.allow(source.name, source.rate_limit_per_minute):
            db.commit()
            raise self.retry(countdown=60)

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        job.retry_count = self.request.retries
        db.commit()

        adapter = _adapter_for(source)
        discovered = updated = failed = 0
        try:
            if not source_target:
                raise ValueError(
                    f"No fetch target supplied for source '{source.name}'. "
                    "Wire the real, ToS-reviewed URL/resource id before dispatching."
                )
            fetch_result = adapter.fetch(source_target)
            records = adapter.parse(fetch_result)
            discovered = len(records)

            for record in records:
                try:
                    result = ingest_parsed_record(db, adapter, source, policy, record)
                    db.commit()
                    if result.decision in ("auto_match", "new_company"):
                        updated += 1
                except Exception:
                    db.rollback()
                    failed += 1
                    logger.exception(
                        "record_ingest_failed",
                        extra={"extra_fields": {"source": source.name, "external_ref": record.external_ref}},
                    )

            job.status = "success" if failed == 0 else "partial"
            job.records_discovered = discovered
            job.records_updated = updated
            job.records_failed = failed
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(
                "source_collection_finished",
                extra={
                    "extra_fields": {
                        "source": source.name,
                        "job_id": str(job.id),
                        "status": job.status,
                        "discovered": discovered,
                        "updated": updated,
                        "failed": failed,
                    }
                },
            )
            return {
                "status": job.status,
                "job_id": str(job.id),
                "discovered": discovered,
                "updated": updated,
                "failed": failed,
            }
        except Exception as exc:
            job.status = "failed"
            job.error_summary = str(exc)[:2000]
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            raise
    finally:
        db.close()


@celery_app.task
def reprocess_company_evidence(company_id: str) -> dict:
    """Reprocessing entry point: recompute Evidence + canonical fields for a
    company from its existing RawObservations, without re-fetching anything.
    Useful after a confidence-rule change or a manual review-queue merge."""
    from app.ingestion.pipeline import recompute_company_evidence

    db = SessionLocal()
    try:
        recompute_company_evidence(db, uuid.UUID(company_id))
        db.commit()
        return {"status": "success", "company_id": company_id}
    finally:
        db.close()
