from datetime import datetime, timedelta, timezone

from app.ingestion.source_health import compute_all_source_health, compute_source_health
from app.models.ingestion_job import IngestionJob


def _job(source_id, **overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        source_id=source_id,
        status="success",
        idempotency_key="key",
        started_at=now,
        finished_at=now,
        records_discovered=1,
        records_updated=1,
        records_failed=0,
    )
    defaults.update(overrides)
    return IngestionJob(**defaults)


class TestComputeSourceHealth:
    def test_source_with_no_jobs_has_no_last_run(self, db, mca_source):
        health = compute_source_health(db, mca_source)
        assert health.last_successful_run is None
        assert health.last_run_status is None
        assert health.last_error is None
        assert health.records_collected_total == 0
        assert health.total_jobs == 0

    def test_last_successful_run_is_most_recent_success(self, db, mca_source):
        older = datetime.now(timezone.utc) - timedelta(days=2)
        newer = datetime.now(timezone.utc) - timedelta(hours=1)
        db.add_all(
            [
                _job(mca_source.id, idempotency_key="a", status="success", finished_at=older, records_updated=5),
                _job(mca_source.id, idempotency_key="b", status="success", finished_at=newer, records_updated=3),
            ]
        )
        db.commit()

        health = compute_source_health(db, mca_source)
        assert health.last_successful_run == newer
        assert health.records_collected_total == 8
        assert health.total_jobs == 2

    def test_last_error_reflects_most_recent_failure_even_after_a_later_success(self, db, mca_source):
        failed_at = datetime.now(timezone.utc) - timedelta(days=1)
        success_at = datetime.now(timezone.utc)
        db.add_all(
            [
                _job(
                    mca_source.id,
                    idempotency_key="fail",
                    status="failed",
                    finished_at=failed_at,
                    error_summary="rate limited",
                    records_updated=0,
                ),
                _job(mca_source.id, idempotency_key="ok", status="success", finished_at=success_at, records_updated=2),
            ]
        )
        db.commit()

        health = compute_source_health(db, mca_source)
        assert health.last_successful_run == success_at
        assert health.last_error == "rate limited"
        assert health.last_run_status == "success"

    def test_source_with_only_failures_has_no_successful_run(self, db, mca_source):
        db.add(_job(mca_source.id, idempotency_key="fail", status="failed", error_summary="timeout", records_updated=0))
        db.commit()

        health = compute_source_health(db, mca_source)
        assert health.last_successful_run is None
        assert health.last_error == "timeout"
        assert health.last_run_status == "failed"


class TestComputeAllSourceHealth:
    def test_includes_every_source(self, db, mca_source, filesure_source):
        results = compute_all_source_health(db)
        names = {h.source.name for h in results}
        assert mca_source.name in names
        assert filesure_source.name in names
