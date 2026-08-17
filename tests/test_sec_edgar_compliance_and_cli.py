"""Tests for the SEC EDGAR compliance gates (User-Agent config +
Source.collection_enabled) and app/cli/sec_edgar_lookup.py's dry-run
behavior. No network calls -- SecEdgarAdapter.fetch() is monkeypatched to
return a fixed FetchResult, so these tests exercise the CLI's orchestration
logic without touching the real API.
"""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.models.company import Company
from app.models.ingestion_job import IngestionJob
from app.models.observation import RawObservation
from app.source_adapters.base import FetchResult
from app.source_adapters.sec_edgar_adapter import SecEdgarAdapter
from app.source_adapters.sec_edgar_client import SecEdgarConfigurationError

VALID_CIK = "0000320193"


def _fake_fetch_result() -> FetchResult:
    envelope = {
        "cik": VALID_CIK,
        "submissions": {
            "cik": VALID_CIK,
            "name": "Test Public Manufacturing Co",
            "sicDescription": "Electronic Computers",
            "sic": "3571",
        },
        "company_facts": None,
        "company_facts_error": "not fetched in this fixture",
        "retrieved_at": "2026-08-14T12:00:00+00:00",
    }
    return FetchResult(
        url=f"https://data.sec.gov/submissions/CIK{VALID_CIK}.json",
        status_code=200,
        content=json.dumps(envelope).encode("utf-8"),
        content_type="application/json",
        fetched_at=datetime.now(timezone.utc),
    )


class TestComplianceGate:
    def test_fetch_raises_when_user_agent_not_configured(self, monkeypatch):
        monkeypatch.setattr(
            "app.source_adapters.sec_edgar_adapter.get_settings",
            lambda: Settings(sec_edgar_user_agent=""),
        )
        adapter = SecEdgarAdapter(source_name="sec_edgar")
        with pytest.raises(SecEdgarConfigurationError, match="SEC_EDGAR_USER_AGENT"):
            adapter.fetch(VALID_CIK)

    def test_disabled_source_row_blocks_ingestion_even_with_user_agent_configured(self, db, monkeypatch):
        """Companies House/FileSure have a config-level gate for a secret
        that must be present; SEC EDGAR has no secret, but the standard
        Source.collection_enabled DB gate must still independently block
        ingestion -- mirrors docs/compliance.md's "two independent places"."""
        from app.compliance.source_policy import CollectionNotPermittedError, SourcePolicy
        from app.ingestion.pipeline import ingest_parsed_record
        from app.models.source import Source

        source = Source(
            name="sec_edgar_disabled_test",
            source_type="public_filing",
            collection_enabled=False,
            rate_limit_per_minute=300,
            max_concurrency=1,
            reliability_weight=95,
        )
        db.add(source)
        db.commit()

        adapter = SecEdgarAdapter(source_name=source.name)
        record = adapter.parse(_fake_fetch_result())[0]
        policy = SourcePolicy(
            source_name=source.name,
            collection_enabled=source.collection_enabled,
            rate_limit_per_minute=source.rate_limit_per_minute,
            max_concurrency=source.max_concurrency,
        )
        with pytest.raises(CollectionNotPermittedError):
            ingest_parsed_record(db, adapter, source, policy, record)


class TestSecEdgarLookupCliDryRun:
    def test_dry_run_writes_nothing_but_source_bootstrap(self, db, monkeypatch):
        from app.cli import sec_edgar_lookup

        monkeypatch.setattr(SecEdgarAdapter, "fetch", lambda self, target: _fake_fetch_result())

        exit_code = sec_edgar_lookup.run(VALID_CIK, dry_run=True)

        assert exit_code == 0
        assert db.scalar(select(Company)) is None
        assert db.scalar(select(RawObservation)) is None
        assert db.scalar(select(IngestionJob)) is None

    def test_real_run_commits_company_and_observations(self, db, monkeypatch):
        from app.cli import sec_edgar_lookup

        monkeypatch.setattr(SecEdgarAdapter, "fetch", lambda self, target: _fake_fetch_result())

        exit_code = sec_edgar_lookup.run(VALID_CIK, dry_run=False)

        assert exit_code == 0
        company = db.scalar(select(Company).where(Company.country_code == "US"))
        assert company is not None
        assert company.canonical_name
        observations = list(db.scalars(select(RawObservation).where(RawObservation.company_id == company.id)))
        assert len(observations) > 0
