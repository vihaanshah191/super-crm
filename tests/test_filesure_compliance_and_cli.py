"""Tests for the FileSure compliance gates (config-level +
Source.collection_enabled) and app/cli/filesure_lookup.py's dry-run
behavior. No network calls -- FileSureAdapter.fetch() is monkeypatched to
return a fixed FetchResult built from the recorded fixture, so these tests
exercise the CLI's orchestration logic (reporting, dry-run rollback) without
touching the real API.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.models.company import Company
from app.models.ingestion_job import IngestionJob
from app.models.observation import RawObservation
from app.source_adapters.base import FetchResult
from app.source_adapters.filesure_adapter import FileSureAdapter
from app.source_adapters.filesure_client import FileSureConfigurationError

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "data"
VALID_CIN = "L74110KA2013PLC096530"


def _fake_fetch_result() -> FetchResult:
    raw = json.loads((FIXTURES_DIR / "filesure_master_data_response.json").read_text())
    envelope = {
        "cin": VALID_CIN,
        "master_data": raw["data"],
        "extractions_raw": None,
        "extractions_error": "extractions endpoint returned 404 in this fixture",
        "retrieved_at": "2026-08-04T12:00:00+00:00",
    }
    return FetchResult(
        url=f"https://api.filesure.in/v1/companies/{VALID_CIN}",
        status_code=200,
        content=json.dumps(envelope).encode("utf-8"),
        content_type="application/json",
        fetched_at=datetime.now(timezone.utc),
    )


class TestComplianceGate:
    def test_fetch_raises_when_collection_disabled(self, monkeypatch):
        monkeypatch.setattr(
            "app.source_adapters.filesure_adapter.get_settings",
            lambda: Settings(filesure_collection_enabled=False, filesure_api_key="fsk_test_x"),
        )
        adapter = FileSureAdapter(source_name="filesure")
        with pytest.raises(FileSureConfigurationError, match="FILESURE_COLLECTION_ENABLED"):
            adapter.fetch(VALID_CIN)

    def test_fetch_raises_when_no_api_key_even_if_enabled(self, monkeypatch):
        monkeypatch.setattr(
            "app.source_adapters.filesure_adapter.get_settings",
            lambda: Settings(filesure_collection_enabled=True, filesure_api_key=""),
        )
        adapter = FileSureAdapter(source_name="filesure")
        with pytest.raises(FileSureConfigurationError, match="FILESURE_API_KEY"):
            adapter.fetch(VALID_CIN)

    def test_disabled_source_row_blocks_ingestion_even_with_key_configured(self, db, monkeypatch):
        """Second, independent gate: even with FILESURE_COLLECTION_ENABLED=true
        and a key configured, a disabled Source DB row must still block
        ingestion -- mirrors docs/compliance.md's "two independent places"."""
        from app.compliance.source_policy import CollectionNotPermittedError, SourcePolicy
        from app.ingestion.pipeline import ingest_parsed_record
        from app.models.source import Source

        source = Source(
            name="filesure_disabled_test",
            source_type="registry_data_provider",
            collection_enabled=False,
            rate_limit_per_minute=30,
            max_concurrency=1,
            reliability_weight=85,
        )
        db.add(source)
        db.commit()

        adapter = FileSureAdapter(source_name=source.name)
        record = adapter.parse(_fake_fetch_result())[0]
        policy = SourcePolicy(
            source_name=source.name,
            collection_enabled=source.collection_enabled,
            rate_limit_per_minute=source.rate_limit_per_minute,
            max_concurrency=source.max_concurrency,
        )
        with pytest.raises(CollectionNotPermittedError):
            ingest_parsed_record(db, adapter, source, policy, record)


class TestFilesureLookupCliDryRun:
    def test_dry_run_writes_nothing_but_source_bootstrap(self, db, monkeypatch):
        from app.cli import filesure_lookup

        monkeypatch.setattr(FileSureAdapter, "fetch", lambda self, target: _fake_fetch_result())

        exit_code = filesure_lookup.run(VALID_CIN, dry_run=True)

        assert exit_code == 0
        assert db.scalar(select(Company)) is None
        assert db.scalar(select(RawObservation)) is None
        assert db.scalar(select(IngestionJob)) is None

    def test_real_run_commits_company_and_observations(self, db, monkeypatch):
        from app.cli import filesure_lookup

        monkeypatch.setattr(FileSureAdapter, "fetch", lambda self, target: _fake_fetch_result())

        exit_code = filesure_lookup.run(VALID_CIN, dry_run=False)

        assert exit_code == 0
        company = db.scalar(select(Company).where(Company.cin == VALID_CIN))
        assert company is not None
        assert company.canonical_name  # normalized_name/canonical_name got set
        observations = list(db.scalars(select(RawObservation).where(RawObservation.company_id == company.id)))
        assert len(observations) > 0

    def test_dry_run_reports_zero_financial_records_with_no_confirmed_schema(self, db, monkeypatch, capsys):
        from app.cli import filesure_lookup

        monkeypatch.setattr(FileSureAdapter, "fetch", lambda self, target: _fake_fetch_result())

        filesure_lookup.run(VALID_CIN, dry_run=True)

        captured = capsys.readouterr()
        assert "0 company_financials records would be created" in captured.out
