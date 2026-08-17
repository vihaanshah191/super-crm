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
from app.ingestion.jobs.tasks import _adapter_for, dispatch_enabled_source_collections, run_source_collection
from app.models.ingestion_job import IngestionJob
from app.models.source import Source
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


class TestDispatchExcludesUploadedFileSources:
    def test_user_uploaded_file_sources_are_never_dispatched(self, db, website_source, monkeypatch):
        """A user_file source (import_custom_source.py, and import_mca.py's
        file-import row) has collection_enabled=True to mean 'this
        collection method is compliance-permitted', not 'Beat should
        periodically re-fetch it' -- there's no URL for source_target and no
        durable local file path to re-fetch from on a schedule. Dispatching
        it used to raise inside run_source_collection (no fetch target)
        every single day."""
        uploaded = Source(
            name="custom_uploaded_source",
            source_type="user_file",
            access_method="user_uploaded_file",
            collection_enabled=True,
            rate_limit_per_minute=10_000,
            max_concurrency=1,
            reliability_weight=30,
        )
        db.add(uploaded)
        db.commit()

        dispatched_args = []
        monkeypatch.setattr(
            "app.ingestion.jobs.tasks.run_source_collection.delay",
            lambda *args: dispatched_args.append(args),
        )

        result = dispatch_enabled_source_collections()

        assert website_source.name in result["dispatched_sources"]
        assert uploaded.name not in result["dispatched_sources"]
        assert all(args[0] != str(uploaded.id) for args in dispatched_args)


class TestAdapterForDispatch:
    """Covers a real dispatch collision found and fixed while adding
    CompaniesHouseAdapter: it shares source_type="government_dataset" with
    the pre-existing GovernmentDatasetAdapter (MCA), so _adapter_for() must
    disambiguate by source.name, not source_type alone."""

    def test_government_dataset_source_named_companies_house_dispatches_to_companies_house_adapter(
        self, companies_house_source
    ):
        from app.source_adapters.companies_house_adapter import CompaniesHouseAdapter

        assert isinstance(_adapter_for(companies_house_source), CompaniesHouseAdapter)

    def test_government_dataset_source_with_other_name_dispatches_to_government_dataset_adapter(self, mca_source):
        from app.source_adapters.government_dataset_adapter import GovernmentDatasetAdapter

        assert isinstance(_adapter_for(mca_source), GovernmentDatasetAdapter)

    def test_public_filing_source_dispatches_to_sec_edgar_adapter(self, sec_edgar_source):
        from app.source_adapters.sec_edgar_adapter import SecEdgarAdapter

        assert isinstance(_adapter_for(sec_edgar_source), SecEdgarAdapter)


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
