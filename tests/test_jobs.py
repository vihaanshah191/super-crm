"""Tests for the Celery task layer, run in eager mode (no worker/broker
round-trip) with the adapter registry monkeypatched so no live fetch happens.
Exponential-backoff retry mechanics themselves are Celery's own library code
and are covered indirectly via ScraplingCollector's retry tests -- these
tests focus on what our task code is responsible for: idempotency, failure
isolation, and per-source enable/disable.
"""

from datetime import datetime, timezone

import pytest

from app.ingestion.jobs.celery_app import celery_app
from app.ingestion.jobs.tasks import run_source_collection
from app.models.ingestion_job import IngestionJob
from app.source_adapters.base import FetchResult, ObservationDraft, ParsedRecord, SourceAdapter


@pytest.fixture(autouse=True)
def eager_celery():
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = False


class _FakeAdapter(SourceAdapter):
    source_type = "website"
    collector_version = "fake/1.0"

    def __init__(self, source_name: str, record_count: int = 1):
        self.source_name = source_name
        self.fetch_calls = 0
        self.record_count = record_count

    def fetch(self, target: str) -> FetchResult:
        self.fetch_calls += 1
        return FetchResult(
            url=target, status_code=200, content=b"", content_type="text/html", fetched_at=datetime.now(timezone.utc)
        )

    def parse(self, fetch_result: FetchResult) -> list[ParsedRecord]:
        return [
            ParsedRecord(external_ref=f"rec-{i}", fields={"canonical_name": f"Test Co {i}"})
            for i in range(self.record_count)
        ]

    def normalize(self, record: ParsedRecord) -> list[ObservationDraft]:
        name = record.fields["canonical_name"]
        return [
            ObservationDraft(
                field="canonical_name", raw_value=name, normalized_value=name.lower(), confidence=0.5,
                verification_type="observed",
            )
        ]


class TestIdempotentIngestion:
    def test_same_idempotency_key_skips_second_dispatch(self, db, website_source, monkeypatch):
        fake = _FakeAdapter(website_source.name)
        monkeypatch.setattr("app.ingestion.jobs.tasks._adapter_for", lambda source: fake)

        r1 = run_source_collection.apply(args=[str(website_source.id), "https://example.test/a", "2026-01-01"]).get()
        r2 = run_source_collection.apply(args=[str(website_source.id), "https://example.test/a", "2026-01-01"]).get()

        assert r1["status"] == "success"
        assert r2["status"] == "skipped"
        assert r2["reason"] == "already_completed"
        assert fake.fetch_calls == 1  # second call never re-fetched
        assert db.query(IngestionJob).filter_by(idempotency_key="2026-01-01").count() == 1

    def test_different_idempotency_key_runs_again(self, db, website_source, monkeypatch):
        fake = _FakeAdapter(website_source.name)
        monkeypatch.setattr("app.ingestion.jobs.tasks._adapter_for", lambda source: fake)

        run_source_collection.apply(args=[str(website_source.id), "https://example.test/a", "2026-01-01"]).get()
        run_source_collection.apply(args=[str(website_source.id), "https://example.test/a", "2026-01-02"]).get()

        assert fake.fetch_calls == 2
        assert db.query(IngestionJob).count() == 2


class TestDisabledSourceIsSkipped:
    def test_disabled_source_never_fetches(self, db, website_source, monkeypatch):
        website_source.collection_enabled = False
        db.commit()
        fake = _FakeAdapter(website_source.name)
        monkeypatch.setattr("app.ingestion.jobs.tasks._adapter_for", lambda source: fake)

        result = run_source_collection.apply(
            args=[str(website_source.id), "https://example.test/a", "2026-01-01"]
        ).get()

        assert result["status"] == "skipped"
        assert result["reason"] == "collection_disabled"
        assert fake.fetch_calls == 0


class TestFailureIsolation:
    def test_one_bad_record_does_not_stop_others_in_the_same_batch(self, db, website_source, monkeypatch):
        fake = _FakeAdapter(website_source.name, record_count=3)

        original_normalize = fake.normalize
        call_count = {"n": 0}

        def flaky_normalize(record):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated bad record")
            return original_normalize(record)

        fake.normalize = flaky_normalize
        monkeypatch.setattr("app.ingestion.jobs.tasks._adapter_for", lambda source: fake)

        result = run_source_collection.apply(
            args=[str(website_source.id), "https://example.test/a", "2026-01-01"]
        ).get()

        assert result["status"] == "partial"
        assert result["discovered"] == 3
        assert result["updated"] == 2
        assert result["failed"] == 1
