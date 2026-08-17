"""Tests for CompaniesHouseAdapter.parse()/validate()/normalize() and its
integration with the real ingestion pipeline (entity resolution, evidence,
provenance). No network calls -- fetch() is never invoked here; a
FetchResult is built directly from a fixture profile shaped exactly like
the confirmed live API reference schema (see
docs/companies_house_data_access.md) -- Companies House's official API
documentation, not a guess.
"""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.compliance.source_policy import SourcePolicy
from app.ingestion.pipeline import ingest_parsed_record
from app.models.company import Company
from app.models.evidence import Evidence
from app.models.observation import RawObservation
from app.source_adapters.base import FetchResult
from app.source_adapters.companies_house_adapter import CompaniesHouseAdapter

VALID_COMPANY_NUMBER = "00000006"

# Shaped exactly per the confirmed API reference field names (see
# docs/companies_house_data_access.md) -- not a real company's data.
SAMPLE_PROFILE = {
    "company_number": VALID_COMPANY_NUMBER,
    "company_name": "TEST MANUFACTURING LIMITED",
    "company_status": "active",
    "type": "ltd",
    "date_of_creation": "2015-04-12",
    "jurisdiction": "england-wales",
    "sic_codes": ["25620", "46190"],
    "registered_office_address": {
        "address_line_1": "1 Test Street",
        "address_line_2": "Test Industrial Estate",
        "locality": "Manchester",
        "region": "Greater Manchester",
        "postal_code": "M1 1AA",
        "country": "United Kingdom",
    },
    "has_charges": False,
    "has_insolvency_history": False,
    "accounts": {"next_due": "2026-12-31"},
    "confirmation_statement": {"next_due": "2026-11-30"},
}


def _fetch_result_from_profile(profile: dict) -> FetchResult:
    envelope = {
        "company_number": profile.get("company_number"),
        "profile": profile,
        "retrieved_at": "2026-08-14T12:00:00+00:00",
    }
    return FetchResult(
        url=f"https://api.company-information.service.gov.uk/company/{VALID_COMPANY_NUMBER}",
        status_code=200,
        content=json.dumps(envelope).encode("utf-8"),
        content_type="application/json",
        fetched_at=datetime.now(timezone.utc),
    )


@pytest.fixture()
def fetch_result() -> FetchResult:
    return _fetch_result_from_profile(SAMPLE_PROFILE)


class TestParse:
    def test_extracts_company_number_as_external_ref(self, fetch_result):
        adapter = CompaniesHouseAdapter(source_name="companies_house")
        records = adapter.parse(fetch_result)
        assert len(records) == 1
        assert records[0].external_ref == VALID_COMPANY_NUMBER

    def test_missing_company_number_produces_no_records(self):
        adapter = CompaniesHouseAdapter(source_name="companies_house")
        fetch_result = _fetch_result_from_profile({"company_name": "No Number Ltd"})
        assert adapter.parse(fetch_result) == []


class TestValidate:
    def test_requires_8_character_company_number(self, fetch_result):
        adapter = CompaniesHouseAdapter(source_name="companies_house")
        record = adapter.parse(fetch_result)[0]
        assert adapter.validate(record) is True

    def test_rejects_short_company_number(self, fetch_result):
        adapter = CompaniesHouseAdapter(source_name="companies_house")
        record = adapter.parse(fetch_result)[0]
        bad_record = record.__class__(external_ref="short", fields={**record.fields, "company_number": "short"})
        assert adapter.validate(bad_record) is False


class TestNormalize:
    def test_asserts_gb_country_code(self, fetch_result):
        adapter = CompaniesHouseAdapter(source_name="companies_house")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d for d in adapter.normalize(record)}
        assert drafts["country_code"].normalized_value == "GB"
        assert drafts["country_code"].verification_type == "verified"

    def test_maps_identity_fields(self, fetch_result):
        adapter = CompaniesHouseAdapter(source_name="companies_house")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d.normalized_value for d in adapter.normalize(record)}
        assert drafts["legal_name"] == "TEST MANUFACTURING LIMITED"
        assert drafts["canonical_name"]
        assert drafts["company_status"] == "active"
        assert drafts["company_type"] == "ltd"
        assert drafts["incorporation_date"] == "2015-04-12"

    def test_sic_code_bucketed_to_manufacturing_industry_section(self, fetch_result):
        """25620 -> division 25 -> SIC 2007 section 10-33 (Manufacturing).
        Raw codes preserved on sub_industry."""
        adapter = CompaniesHouseAdapter(source_name="companies_house")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d.normalized_value for d in adapter.normalize(record)}
        assert drafts["industry"] == "Manufacturing"
        assert drafts["sub_industry"] == "25620, 46190"

    def test_maps_registered_address(self, fetch_result):
        adapter = CompaniesHouseAdapter(source_name="companies_house")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d.normalized_value for d in adapter.normalize(record)}
        assert drafts["city"] == "Manchester"
        assert drafts["state"] == "Greater Manchester"
        assert drafts["postal_code"] == "M1 1AA"
        assert "1 Test Street" in drafts["registered_address"]

    def test_filing_metadata_is_observed_not_verified(self, fetch_result):
        """Filing due-dates are administrative metadata, not the registrar
        directly asserting a fact about the company the way company_status
        is -- OBSERVED, one notch below VERIFIED."""
        adapter = CompaniesHouseAdapter(source_name="companies_house")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d for d in adapter.normalize(record)}
        assert drafts["accounts_next_due"].normalized_value == "2026-12-31"
        assert drafts["accounts_next_due"].verification_type == "observed"
        assert drafts["confirmation_statement_next_due"].normalized_value == "2026-11-30"

    def test_has_charges_and_insolvency_flags_present_even_when_false(self, fetch_result):
        """False is a real, meaningful value here (not 'unknown') -- must
        not be dropped by a truthiness check."""
        adapter = CompaniesHouseAdapter(source_name="companies_house")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d.normalized_value for d in adapter.normalize(record)}
        assert drafts["has_charges"] == "false"
        assert drafts["has_insolvency_history"] == "false"

    def test_no_revenue_or_employee_observations_are_ever_produced(self, fetch_result):
        """Companies House's company-profile endpoint does not return
        filed financial figures or employee counts -- confirmed by reading
        the documented response shape (docs/companies_house_data_access.md).
        This adapter must never fabricate them."""
        adapter = CompaniesHouseAdapter(source_name="companies_house")
        record = adapter.parse(fetch_result)[0]
        fields = {d.field for d in adapter.normalize(record)}
        assert "annual_revenue_inr" not in fields
        assert "employee_count" not in fields

    def test_missing_sic_codes_does_not_crash(self):
        adapter = CompaniesHouseAdapter(source_name="companies_house")
        profile = {**SAMPLE_PROFILE, "sic_codes": []}
        record = adapter.parse(_fetch_result_from_profile(profile))[0]
        drafts = adapter.normalize(record)  # must not raise
        assert not any(d.field == "industry" for d in drafts)


class TestEntityResolutionAndProvenance:
    def test_new_company_number_creates_a_new_company(self, db, companies_house_source, fetch_result):
        adapter = CompaniesHouseAdapter(source_name=companies_house_source.name)
        record = adapter.parse(fetch_result)[0]
        policy = SourcePolicy(
            source_name=companies_house_source.name,
            collection_enabled=companies_house_source.collection_enabled,
            rate_limit_per_minute=companies_house_source.rate_limit_per_minute,
            max_concurrency=companies_house_source.max_concurrency,
        )
        result = ingest_parsed_record(db, adapter, companies_house_source, policy, record)
        db.commit()

        assert result.decision == "new_company"
        company = db.get(Company, result.company_id)
        assert company.country_code == "GB"
        assert company.legal_name == "TEST MANUFACTURING LIMITED"
        assert company.industry == "Manufacturing"

    def test_evidence_rows_link_back_to_the_raw_observation(self, db, companies_house_source, fetch_result):
        adapter = CompaniesHouseAdapter(source_name=companies_house_source.name)
        record = adapter.parse(fetch_result)[0]
        policy = SourcePolicy(
            source_name=companies_house_source.name,
            collection_enabled=companies_house_source.collection_enabled,
            rate_limit_per_minute=companies_house_source.rate_limit_per_minute,
            max_concurrency=companies_house_source.max_concurrency,
        )
        result = ingest_parsed_record(db, adapter, companies_house_source, policy, record)
        db.commit()

        observations = list(db.scalars(select(RawObservation).where(RawObservation.company_id == result.company_id)))
        assert any(o.field == "legal_name" for o in observations)
        assert all(o.source_id == companies_house_source.id for o in observations)

        evidence_rows = list(db.scalars(select(Evidence).where(Evidence.company_id == result.company_id)))
        assert any(e.field == "legal_name" and e.verification_type == "verified" for e in evidence_rows)
