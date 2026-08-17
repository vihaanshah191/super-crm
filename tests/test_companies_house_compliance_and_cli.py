"""Tests for the Companies House compliance gates (config-level +
Source.collection_enabled) and app/cli/companies_house_lookup.py's dry-run
behavior. No network calls -- CompaniesHouseAdapter.fetch() is monkeypatched
to return a fixed FetchResult, so these tests exercise the CLI's
orchestration logic without touching the real API.
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
from app.source_adapters.companies_house_adapter import CompaniesHouseAdapter
from app.source_adapters.companies_house_client import CompaniesHouseConfigurationError

VALID_COMPANY_NUMBER = "00000006"


def _fake_fetch_result() -> FetchResult:
    envelope = {
        "company_number": VALID_COMPANY_NUMBER,
        "profile": {
            "company_number": VALID_COMPANY_NUMBER,
            "company_name": "TEST MANUFACTURING LIMITED",
            "company_status": "active",
            "type": "ltd",
            "date_of_creation": "2015-04-12",
            "sic_codes": ["25620"],
            "registered_office_address": {"locality": "Manchester", "postal_code": "M1 1AA"},
        },
        "retrieved_at": "2026-08-14T12:00:00+00:00",
    }
    return FetchResult(
        url=f"https://api.company-information.service.gov.uk/company/{VALID_COMPANY_NUMBER}",
        status_code=200,
        content=json.dumps(envelope).encode("utf-8"),
        content_type="application/json",
        fetched_at=datetime.now(timezone.utc),
    )


class TestComplianceGate:
    def test_fetch_raises_when_collection_disabled(self, monkeypatch):
        monkeypatch.setattr(
            "app.source_adapters.companies_house_adapter.get_settings",
            lambda: Settings(companies_house_collection_enabled=False, companies_house_api_key="ch_test_x"),
        )
        adapter = CompaniesHouseAdapter(source_name="companies_house")
        with pytest.raises(CompaniesHouseConfigurationError, match="COMPANIES_HOUSE_COLLECTION_ENABLED"):
            adapter.fetch(VALID_COMPANY_NUMBER)

    def test_fetch_raises_when_no_api_key_even_if_enabled(self, monkeypatch):
        monkeypatch.setattr(
            "app.source_adapters.companies_house_adapter.get_settings",
            lambda: Settings(companies_house_collection_enabled=True, companies_house_api_key=""),
        )
        adapter = CompaniesHouseAdapter(source_name="companies_house")
        with pytest.raises(CompaniesHouseConfigurationError, match="COMPANIES_HOUSE_API_KEY"):
            adapter.fetch(VALID_COMPANY_NUMBER)

    def test_disabled_source_row_blocks_ingestion_even_with_key_configured(self, db, monkeypatch):
        """Second, independent gate: even with COMPANIES_HOUSE_COLLECTION_ENABLED=true
        and a key configured, a disabled Source DB row must still block
        ingestion -- mirrors docs/compliance.md's "two independent places"."""
        from app.compliance.source_policy import CollectionNotPermittedError, SourcePolicy
        from app.ingestion.pipeline import ingest_parsed_record
        from app.models.source import Source

        source = Source(
            name="companies_house_disabled_test",
            source_type="government_dataset",
            collection_enabled=False,
            rate_limit_per_minute=120,
            max_concurrency=1,
            reliability_weight=95,
        )
        db.add(source)
        db.commit()

        adapter = CompaniesHouseAdapter(source_name=source.name)
        record = adapter.parse(_fake_fetch_result())[0]
        policy = SourcePolicy(
            source_name=source.name,
            collection_enabled=source.collection_enabled,
            rate_limit_per_minute=source.rate_limit_per_minute,
            max_concurrency=source.max_concurrency,
        )
        with pytest.raises(CollectionNotPermittedError):
            ingest_parsed_record(db, adapter, source, policy, record)


class TestCompaniesHouseLookupCliDryRun:
    def test_dry_run_writes_nothing_but_source_bootstrap(self, db, monkeypatch):
        from app.cli import companies_house_lookup

        monkeypatch.setattr(CompaniesHouseAdapter, "fetch", lambda self, target: _fake_fetch_result())

        exit_code = companies_house_lookup.run(VALID_COMPANY_NUMBER, dry_run=True)

        assert exit_code == 0
        assert db.scalar(select(Company)) is None
        assert db.scalar(select(RawObservation)) is None
        assert db.scalar(select(IngestionJob)) is None

    def test_real_run_commits_company_and_observations(self, db, monkeypatch):
        from app.cli import companies_house_lookup

        monkeypatch.setattr(CompaniesHouseAdapter, "fetch", lambda self, target: _fake_fetch_result())

        exit_code = companies_house_lookup.run(VALID_COMPANY_NUMBER, dry_run=False)

        assert exit_code == 0
        company = db.scalar(select(Company).where(Company.country_code == "GB"))
        assert company is not None
        assert company.canonical_name
        observations = list(db.scalars(select(RawObservation).where(RawObservation.company_id == company.id)))
        assert len(observations) > 0
