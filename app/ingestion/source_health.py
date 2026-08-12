"""Derives per-source health/status purely from existing IngestionJob rows.

No new Source columns for this (see docs/multi_source_architecture.md,
Section G): `last_successful_run`/`last_error`/`records_collected` are
deliberately not stored redundantly on `Source` -- IngestionJob already has
everything needed (status, finished_at, records_updated, error_summary),
and a duplicated copy could drift out of sync with the job history it's
supposed to summarize. This is a read-only projection over that table, not
new state.

`compliance_status` (ACTIVE/REQUIRES_LICENSE/NOT_AVAILABLE/UNDER_REVIEW,
see the assessment doc) isn't recomputed here -- it's a genuinely new,
first-class fact about a source (not derivable from job history), stored
directly on `Source` (see the `5c66876cc0f9` migration) and exposed as-is
through `SourceHealth.source.compliance_status` / `SourceOut` rather than
duplicated onto this dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ingestion_job import IngestionJob
from app.models.source import Source


@dataclass(frozen=True)
class SourceHealth:
    source: Source
    last_successful_run: datetime | None
    last_run_status: str | None
    last_run_at: datetime | None
    # The most recent *failed* job's error, independent of whether a later
    # run succeeded -- an admin auditing a source wants to know it has
    # failed before even if it's currently green, not just today's status.
    last_error: str | None
    records_collected_total: int
    total_jobs: int


def compute_source_health(db: Session, source: Source) -> SourceHealth:
    jobs = list(
        db.scalars(
            select(IngestionJob).where(IngestionJob.source_id == source.id).order_by(IngestionJob.created_at.desc())
        )
    )
    last_job = jobs[0] if jobs else None
    last_success = next((j for j in jobs if j.status == "success"), None)
    last_failed = next((j for j in jobs if j.status == "failed"), None)

    return SourceHealth(
        source=source,
        last_successful_run=last_success.finished_at if last_success else None,
        last_run_status=last_job.status if last_job else None,
        last_run_at=last_job.started_at if last_job else None,
        last_error=last_failed.error_summary if last_failed else None,
        records_collected_total=sum(j.records_updated for j in jobs),
        total_jobs=len(jobs),
    )


def compute_all_source_health(db: Session) -> list[SourceHealth]:
    sources = list(db.scalars(select(Source).order_by(Source.name)))
    return [compute_source_health(db, s) for s in sources]
