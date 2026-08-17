"""Tests for SecEdgarAdapter.parse()/validate()/normalize() and its
integration with the real ingestion pipeline. No network calls -- fetch()
is never invoked here; a FetchResult is built directly from a fixture
shaped exactly like the confirmed live response schema (see
docs/sec_edgar_data_access.md -- verified against a real live call, not
just documentation)."""

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
from app.source_adapters.sec_edgar_adapter import SecEdgarAdapter

VALID_CIK = "0000320193"

# Shaped per the live-verified schema (docs/sec_edgar_data_access.md) --
# synthetic values, not a real filer's data.
SAMPLE_SUBMISSIONS = {
    "cik": VALID_CIK,
    "entityType": "operating",
    "sic": "3571",
    "sicDescription": "Electronic Computers",
    "name": "Test Public Manufacturing Co",
    "website": "https://testpublicco.example",
    "phone": "(555) 123-4567",
    "addresses": {
        "business": {
            "street1": "1 Test Park Way",
            "street2": None,
            "city": "Testville",
            "stateOrCountry": "CA",
            "zipCode": "95014",
        }
    },
    "filings": {
        "recent": {
            "form": ["10-K", "10-Q"],
            "filingDate": ["2024-02-01", "2023-11-01"],
            "accessionNumber": ["0001-24-000001", "0001-23-000099"],
        }
    },
}

SAMPLE_COMPANY_FACTS = {
    "cik": VALID_CIK,
    "entityName": "Test Public Manufacturing Co",
    "facts": {
        "us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {
                    "USD": [
                        {
                            "start": "2023-01-01",
                            "end": "2023-12-31",
                            "val": 250_000_000,
                            "fy": 2023,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2024-02-01",
                            "accn": "0001-24-000001",
                        }
                    ]
                }
            }
        }
    },
}


def _fetch_result(submissions: dict, company_facts: dict | None = SAMPLE_COMPANY_FACTS) -> FetchResult:
    envelope = {
        "cik": submissions.get("cik"),
        "submissions": submissions,
        "company_facts": company_facts,
        "company_facts_error": None if company_facts is not None else "status=500",
        "retrieved_at": "2026-08-14T12:00:00+00:00",
    }
    return FetchResult(
        url=f"https://data.sec.gov/submissions/CIK{VALID_CIK}.json",
        status_code=200,
        content=json.dumps(envelope).encode("utf-8"),
        content_type="application/json",
        fetched_at=datetime.now(timezone.utc),
    )


@pytest.fixture()
def fetch_result() -> FetchResult:
    return _fetch_result(SAMPLE_SUBMISSIONS)


class TestParse:
    def test_extracts_cik_as_external_ref(self, fetch_result):
        adapter = SecEdgarAdapter(source_name="sec_edgar")
        records = adapter.parse(fetch_result)
        assert len(records) == 1
        assert records[0].external_ref == VALID_CIK

    def test_missing_cik_produces_no_records(self):
        adapter = SecEdgarAdapter(source_name="sec_edgar")
        assert adapter.parse(_fetch_result({"name": "No CIK Co"})) == []


class TestValidate:
    def test_requires_numeric_cik(self, fetch_result):
        adapter = SecEdgarAdapter(source_name="sec_edgar")
        record = adapter.parse(fetch_result)[0]
        assert adapter.validate(record) is True

    def test_rejects_non_numeric_cik(self, fetch_result):
        adapter = SecEdgarAdapter(source_name="sec_edgar")
        record = adapter.parse(fetch_result)[0]
        bad_record = record.__class__(external_ref="bad", fields={**record.fields, "cik": "NOTANUMBER"})
        assert adapter.validate(bad_record) is False


class TestNormalize:
    def test_asserts_us_country_code(self, fetch_result):
        adapter = SecEdgarAdapter(source_name="sec_edgar")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d for d in adapter.normalize(record)}
        assert drafts["country_code"].normalized_value == "US"
        assert drafts["country_code"].verification_type == "verified"

    def test_maps_identity_and_industry_fields(self, fetch_result):
        adapter = SecEdgarAdapter(source_name="sec_edgar")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d.normalized_value for d in adapter.normalize(record)}
        assert drafts["legal_name"] == "Test Public Manufacturing Co"
        assert drafts["canonical_name"]
        assert drafts["industry"] == "Electronic Computers"
        assert drafts["sub_industry"] == "3571"
        assert drafts["website"] == "https://testpublicco.example"
        assert drafts["public_phone"] == "(555) 123-4567"

    def test_maps_business_address(self, fetch_result):
        adapter = SecEdgarAdapter(source_name="sec_edgar")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d.normalized_value for d in adapter.normalize(record)}
        assert drafts["city"] == "Testville"
        assert drafts["state"] == "CA"
        assert drafts["postal_code"] == "95014"
        assert "1 Test Park Way" in drafts["registered_address"]

    def test_revenue_recorded_as_annual_revenue_usd_not_inr(self, fetch_result):
        """Revenue must never land on annual_revenue_inr -- that column is
        India-currency-specific; this is a real currency-mislabeling bug
        class, not a rounding shortcut."""
        adapter = SecEdgarAdapter(source_name="sec_edgar")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d for d in adapter.normalize(record)}
        assert "annual_revenue_usd" in drafts
        assert drafts["annual_revenue_usd"].normalized_value == "250000000"
        assert drafts["annual_revenue_usd"].metadata["currency"] == "USD"
        assert drafts["annual_revenue_usd"].metadata["fiscal_year"] == 2023
        assert "annual_revenue_inr" not in drafts

    def test_no_employee_count_or_incorporation_date_are_ever_produced(self, fetch_result):
        """Confirmed absent from the live schema (docs/sec_edgar_data_access.md)
        -- this adapter must never fabricate them."""
        adapter = SecEdgarAdapter(source_name="sec_edgar")
        record = adapter.parse(fetch_result)[0]
        fields = {d.field for d in adapter.normalize(record)}
        assert "employee_count" not in fields
        assert "incorporation_date" not in fields

    def test_latest_10k_filing_recorded_as_evidence_only_field(self, fetch_result):
        adapter = SecEdgarAdapter(source_name="sec_edgar")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d for d in adapter.normalize(record)}
        assert drafts["latest_annual_filing"].normalized_value == "2024-02-01"
        assert drafts["latest_annual_filing"].verification_type == "observed"
        assert drafts["latest_annual_filing"].metadata["form"] == "10-K"

    def test_missing_company_facts_does_not_crash_and_produces_no_revenue(self):
        adapter = SecEdgarAdapter(source_name="sec_edgar")
        fetch_result = _fetch_result(SAMPLE_SUBMISSIONS, company_facts=None)
        record = adapter.parse(fetch_result)[0]
        drafts = adapter.normalize(record)  # must not raise
        assert not any(d.field == "annual_revenue_usd" for d in drafts)

    def test_empty_filings_does_not_crash(self):
        adapter = SecEdgarAdapter(source_name="sec_edgar")
        submissions = {**SAMPLE_SUBMISSIONS, "filings": {"recent": {}}}
        record = adapter.parse(_fetch_result(submissions))[0]
        drafts = adapter.normalize(record)  # must not raise
        assert not any(d.field == "latest_annual_filing" for d in drafts)


class TestEntityResolutionAndProvenance:
    def test_new_cik_creates_a_new_company(self, db, sec_edgar_source, fetch_result):
        adapter = SecEdgarAdapter(source_name=sec_edgar_source.name)
        record = adapter.parse(fetch_result)[0]
        policy = SourcePolicy(
            source_name=sec_edgar_source.name,
            collection_enabled=sec_edgar_source.collection_enabled,
            rate_limit_per_minute=sec_edgar_source.rate_limit_per_minute,
            max_concurrency=sec_edgar_source.max_concurrency,
        )
        result = ingest_parsed_record(db, adapter, sec_edgar_source, policy, record)
        db.commit()

        assert result.decision == "new_company"
        company = db.get(Company, result.company_id)
        assert company.country_code == "US"
        assert company.legal_name == "Test Public Manufacturing Co"
        # Revenue is Evidence-only (see test_revenue_recorded_as_annual_revenue_usd_not_inr)
        # -- must NOT have been projected onto the INR-specific column.
        assert company.annual_revenue_inr is None

    def test_revenue_evidence_is_preserved_with_full_provenance(self, db, sec_edgar_source, fetch_result):
        adapter = SecEdgarAdapter(source_name=sec_edgar_source.name)
        record = adapter.parse(fetch_result)[0]
        policy = SourcePolicy(
            source_name=sec_edgar_source.name,
            collection_enabled=sec_edgar_source.collection_enabled,
            rate_limit_per_minute=sec_edgar_source.rate_limit_per_minute,
            max_concurrency=sec_edgar_source.max_concurrency,
        )
        result = ingest_parsed_record(db, adapter, sec_edgar_source, policy, record)
        db.commit()

        evidence_rows = list(db.scalars(select(Evidence).where(Evidence.company_id == result.company_id)))
        revenue_evidence = next(e for e in evidence_rows if e.field == "annual_revenue_usd")
        assert revenue_evidence.value == "250000000"
        assert revenue_evidence.verification_type == "observed"

        observations = list(db.scalars(select(RawObservation).where(RawObservation.company_id == result.company_id)))
        assert all(o.source_id == sec_edgar_source.id for o in observations)
