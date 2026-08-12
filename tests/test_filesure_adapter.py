"""Tests for FileSureAdapter.parse()/validate()/normalize() and its
integration with the real ingestion pipeline (entity resolution, evidence,
provenance). No network calls -- fetch() is never invoked here; a
FetchResult is built directly from the recorded fixture, the same pattern
tests/test_adapters.py uses for the other adapters.

tests/fixtures/data/filesure_master_data_response.json is FileSure's own
documented example response (Swiggy Limited, CIN L74110KA2013PLC096530),
recorded from their developer-portal docs -- see
docs/filesure_data_access.md. It is test data, not evidence that FileSure's
live schema still matches exactly.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.compliance.source_policy import SourcePolicy
from app.ingestion.pipeline import ingest_parsed_record
from app.models.company import Company
from app.models.evidence import Evidence
from app.models.observation import RawObservation
from app.source_adapters.base import FetchResult
from app.source_adapters.filesure_adapter import FileSureAdapter

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "data"
VALID_CIN = "L74110KA2013PLC096530"


def _fetch_result_from_master_data(master_data: dict, extractions_raw: dict | None = None) -> FetchResult:
    envelope = {
        "cin": VALID_CIN,
        "master_data": master_data,
        "extractions_raw": extractions_raw,
        "extractions_error": None if extractions_raw is not None else "extractions endpoint returned 404",
        "retrieved_at": "2026-08-04T12:00:00+00:00",
    }
    return FetchResult(
        url=f"https://api.filesure.in/v1/companies/{VALID_CIN}",
        status_code=200,
        content=json.dumps(envelope).encode("utf-8"),
        content_type="application/json",
        fetched_at=datetime.now(timezone.utc),
    )


@pytest.fixture()
def swiggy_master_data() -> dict:
    raw = json.loads((FIXTURES_DIR / "filesure_master_data_response.json").read_text())
    return raw["data"]


@pytest.fixture()
def fetch_result(swiggy_master_data) -> FetchResult:
    return _fetch_result_from_master_data(swiggy_master_data)


@pytest.fixture()
def live_master_data() -> dict:
    """A real sandbox response (2026-08-06), field-scrubbed of email
    addresses only -- see docs/filesure_data_access.md, "Live sandbox
    verification". Its schema differs meaningfully from FileSure's own docs
    example: cin/company/companyStatus are NOT inside companyData live;
    paidupCapital is paidUpCapital; address field names differ."""
    raw = json.loads((FIXTURES_DIR / "filesure_master_data_response_live.json").read_text())
    return raw["data"]


@pytest.fixture()
def live_fetch_result(live_master_data) -> FetchResult:
    return _fetch_result_from_master_data(live_master_data)


class TestParseAgainstLiveConfirmedSchema:
    """Regression coverage for the real schema drift found during live
    sandbox verification (see docs/filesure_data_access.md) -- these would
    all have failed against the pre-fix parse() logic, which assumed
    cin/companyName/companyStatus lived inside companyData."""

    def test_cin_comes_from_top_level_not_company_data(self, live_fetch_result):
        adapter = FileSureAdapter(source_name="filesure")
        records = adapter.parse(live_fetch_result)
        assert len(records) == 1
        assert records[0].external_ref == VALID_CIN
        assert records[0].fields["cin"] == VALID_CIN

    def test_company_name_comes_from_top_level(self, live_fetch_result):
        adapter = FileSureAdapter(source_name="filesure")
        record = adapter.parse(live_fetch_result)[0]
        assert record.fields["company_name"] == "SWIGGY LIMITED"

    def test_company_status_comes_from_common_data(self, live_fetch_result):
        adapter = FileSureAdapter(source_name="filesure")
        record = adapter.parse(live_fetch_result)[0]
        assert record.fields["company_status"] == "Active"

    def test_paid_up_capital_camelcase_variant_is_mapped(self, live_fetch_result):
        adapter = FileSureAdapter(source_name="filesure")
        record = adapter.parse(live_fetch_result)[0]
        assert "paidup_capital" in record.fields
        assert record.fields["paidup_capital"]

    def test_normalize_produces_observations_without_crashing(self, live_fetch_result):
        adapter = FileSureAdapter(source_name="filesure")
        record = adapter.parse(live_fetch_result)[0]
        assert adapter.validate(record) is True
        drafts = adapter.normalize(record)
        fields = {d.field for d in drafts}
        assert "cin" in fields
        assert "legal_name" in fields
        assert "company_status" in fields
        assert "authorized_capital_inr" in fields
        assert "paidup_capital_inr" in fields

    def test_address_alternate_field_names_are_read(self, live_fetch_result):
        """Live companyData.MCAMDSCompanyAddress uses streetAddress/postalCode,
        not the docs example's addressLine1/pinCode."""
        adapter = FileSureAdapter(source_name="filesure")
        record = adapter.parse(live_fetch_result)[0]
        drafts = {d.field: d.normalized_value for d in adapter.normalize(record)}
        assert drafts.get("city") == "Bengaluru"
        assert drafts.get("state") == "karnataka"
        assert drafts.get("postal_code")

    def test_full_ingestion_pipeline_accepts_live_shaped_record(self, db, filesure_source, live_fetch_result):
        adapter = FileSureAdapter(source_name=filesure_source.name)
        record = adapter.parse(live_fetch_result)[0]
        policy = SourcePolicy(
            source_name=filesure_source.name,
            collection_enabled=filesure_source.collection_enabled,
            rate_limit_per_minute=filesure_source.rate_limit_per_minute,
            max_concurrency=filesure_source.max_concurrency,
        )
        result = ingest_parsed_record(db, adapter, filesure_source, policy, record)
        db.commit()

        assert result.decision == "new_company"
        company = db.get(Company, result.company_id)
        assert company.cin == VALID_CIN
        assert company.legal_name == "SWIGGY LIMITED"


class TestParse:
    def test_parse_extracts_one_record_keyed_by_cin(self, fetch_result):
        adapter = FileSureAdapter(source_name="filesure")
        records = adapter.parse(fetch_result)
        assert len(records) == 1
        assert records[0].external_ref == VALID_CIN

    def test_parse_returns_empty_list_when_cin_missing(self):
        """CIN is confirmed (live-verified) to live at the top level of the
        `data` object, not inside companyData -- see
        docs/filesure_data_access.md. A response missing it entirely (not
        just missing companyData) is the actual "nothing usable" case."""
        adapter = FileSureAdapter(source_name="filesure")
        empty = _fetch_result_from_master_data({"cin": "", "masterData": {}})
        assert adapter.parse(empty) == []

    def test_parse_still_produces_a_minimal_record_when_only_cin_and_no_master_data(self):
        """Even with an empty/missing masterData section, a present CIN is
        enough to produce a record -- entity resolution can still attach it
        by CIN even if no other field is available."""
        adapter = FileSureAdapter(source_name="filesure")
        minimal = _fetch_result_from_master_data({"cin": VALID_CIN, "masterData": {}})
        records = adapter.parse(minimal)
        assert len(records) == 1
        assert records[0].external_ref == VALID_CIN

    def test_parse_maps_known_fields_to_canonical_keys(self, fetch_result):
        adapter = FileSureAdapter(source_name="filesure")
        record = adapter.parse(fetch_result)[0]
        assert record.fields["company_name"] == "SWIGGY LIMITED"
        assert record.fields["company_status"] == "Active"
        assert record.fields["authorized_capital"] == "250000000000"
        assert record.fields["paidup_capital"] == "22390000000"
        assert record.fields["pan"] == "AAFCB7707N"


class TestValidate:
    def test_valid_record_passes(self, fetch_result):
        adapter = FileSureAdapter(source_name="filesure")
        record = adapter.parse(fetch_result)[0]
        assert adapter.validate(record) is True

    def test_malformed_cin_fails_validation(self, fetch_result):
        adapter = FileSureAdapter(source_name="filesure")
        record = adapter.parse(fetch_result)[0]
        bad_record = record.__class__(external_ref="short", fields={**record.fields, "cin": "TOOSHORT"})
        assert adapter.validate(bad_record) is False


class TestNormalize:
    def test_produces_verified_observations_for_master_data(self, fetch_result):
        adapter = FileSureAdapter(source_name="filesure")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d for d in adapter.normalize(record)}

        assert drafts["cin"].normalized_value == VALID_CIN
        assert drafts["cin"].verification_type == "verified"
        assert drafts["legal_name"].normalized_value == "SWIGGY LIMITED"
        assert drafts["company_status"].normalized_value == "active"
        assert drafts["incorporation_date"].normalized_value == "2013-12-09"  # DD/MM/YYYY -> ISO

    def test_normalize_asserts_india_country_code(self, fetch_result):
        """FileSure resells India's MCA registry (CIN is an MCA/India
        identifier) -- country_code should be asserted unconditionally so
        country_scope-restricted saved searches don't silently exclude real
        Indian companies collected via FileSure."""
        adapter = FileSureAdapter(source_name="filesure")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d for d in adapter.normalize(record)}
        assert drafts["country_code"].normalized_value == "IN"
        assert drafts["country_code"].verification_type == "verified"

    def test_capital_fields_are_never_labeled_revenue(self, fetch_result):
        """The task's central financial-data requirement: authorized/paid-up
        capital must never be mapped to a revenue field."""
        adapter = FileSureAdapter(source_name="filesure")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field for d in adapter.normalize(record)}
        assert "authorized_capital_inr" in drafts
        assert "paidup_capital_inr" in drafts
        assert "revenue" not in drafts
        assert "annual_revenue_inr" not in drafts

    def test_structured_address_produces_city_state_postal_code(self, fetch_result):
        adapter = FileSureAdapter(source_name="filesure")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d.normalized_value for d in adapter.normalize(record)}
        assert drafts["city"] == "Bengaluru"
        assert drafts["state"] == "karnataka"
        assert drafts["postal_code"] == "560076"

    def test_observation_metadata_records_provenance(self, fetch_result):
        """Provenance chain requirement: every observation must record the
        provider and what FileSure itself claims as the underlying source."""
        adapter = FileSureAdapter(source_name="filesure")
        record = adapter.parse(fetch_result)[0]
        drafts = adapter.normalize(record)
        for draft in drafts:
            assert draft.metadata["provider"] == "filesure"
            assert "underlying_source" in draft.metadata
            assert draft.metadata["retrieved_at"] == "2026-08-04T12:00:00+00:00"

    def test_no_financial_observations_without_confirmed_extraction_schema(self, fetch_result):
        """No confirmed /extractions schema exists yet (see
        docs/filesure_data_access.md) -- normalize() must not fabricate
        financial-year observations from an unmapped raw payload."""
        adapter = FileSureAdapter(source_name="filesure")
        record = adapter.parse(fetch_result)[0]
        drafts = adapter.normalize(record)
        financial_fields = {"revenue_fy", "turnover", "profit", "net_profit", "total_income"}
        assert not any(d.field in financial_fields for d in drafts)

    def test_unmapped_future_extraction_fields_do_not_crash(self, swiggy_master_data):
        """If/when FileSure's extractions endpoint returns data, an
        unrecognized shape must not crash normalize() -- it's simply not
        mapped yet."""
        adapter = FileSureAdapter(source_name="filesure")
        fetch_result = _fetch_result_from_master_data(
            swiggy_master_data, extractions_raw={"someFutureField": {"nested": [1, 2, 3]}}
        )
        record = adapter.parse(fetch_result)[0]
        drafts = adapter.normalize(record)  # must not raise
        assert len(drafts) > 0


class TestEntityResolutionAndProvenance:
    def test_new_cin_creates_a_new_company(self, db, filesure_source, fetch_result):
        adapter = FileSureAdapter(source_name=filesure_source.name)
        record = adapter.parse(fetch_result)[0]
        policy = SourcePolicy(
            source_name=filesure_source.name,
            collection_enabled=filesure_source.collection_enabled,
            rate_limit_per_minute=filesure_source.rate_limit_per_minute,
            max_concurrency=filesure_source.max_concurrency,
        )
        result = ingest_parsed_record(db, adapter, filesure_source, policy, record)
        db.commit()

        assert result.decision == "new_company"
        company = db.get(Company, result.company_id)
        assert company.cin == VALID_CIN

    def test_same_cin_ingested_twice_does_not_duplicate_company(self, db, filesure_source, fetch_result):
        adapter = FileSureAdapter(source_name=filesure_source.name)
        record = adapter.parse(fetch_result)[0]
        policy = SourcePolicy(
            source_name=filesure_source.name,
            collection_enabled=filesure_source.collection_enabled,
            rate_limit_per_minute=filesure_source.rate_limit_per_minute,
            max_concurrency=filesure_source.max_concurrency,
        )

        first = ingest_parsed_record(db, adapter, filesure_source, policy, record)
        db.commit()
        second = ingest_parsed_record(db, adapter, filesure_source, policy, record)
        db.commit()

        assert first.decision == "new_company"
        assert second.decision == "auto_match"
        assert first.company_id == second.company_id
        assert db.scalar(select(Company).where(Company.cin == VALID_CIN)) is not None
        all_companies = list(db.scalars(select(Company).where(Company.cin == VALID_CIN)))
        assert len(all_companies) == 1

    def test_existing_mca_company_with_same_cin_is_auto_matched_not_duplicated(
        self, db, filesure_source, mca_source, fetch_result
    ):
        """Cross-source identity: an MCA-sourced company with the same CIN
        must be the SAME company FileSure attaches to, not a duplicate."""
        existing = Company(canonical_name="Swiggy Limited", normalized_name="swiggy limited", cin=VALID_CIN, confidence=0.9)
        db.add(existing)
        db.commit()

        adapter = FileSureAdapter(source_name=filesure_source.name)
        record = adapter.parse(fetch_result)[0]
        policy = SourcePolicy(
            source_name=filesure_source.name,
            collection_enabled=filesure_source.collection_enabled,
            rate_limit_per_minute=filesure_source.rate_limit_per_minute,
            max_concurrency=filesure_source.max_concurrency,
        )
        result = ingest_parsed_record(db, adapter, filesure_source, policy, record)
        db.commit()

        assert result.decision == "auto_match"
        assert result.company_id == existing.id
        assert db.scalar(select(Company).where(Company.cin == VALID_CIN)) is not None
        assert len(list(db.scalars(select(Company).where(Company.cin == VALID_CIN)))) == 1

    def test_evidence_chain_traces_back_to_filesure_observation(self, db, filesure_source, fetch_result):
        """Answers "why does Super CRM say X" -- Evidence -> EvidenceObservation
        -> RawObservation -> source_id -> Source(filesure), with FileSure's
        provenance metadata preserved on the RawObservation itself."""
        adapter = FileSureAdapter(source_name=filesure_source.name)
        record = adapter.parse(fetch_result)[0]
        policy = SourcePolicy(
            source_name=filesure_source.name,
            collection_enabled=filesure_source.collection_enabled,
            rate_limit_per_minute=filesure_source.rate_limit_per_minute,
            max_concurrency=filesure_source.max_concurrency,
        )
        result = ingest_parsed_record(db, adapter, filesure_source, policy, record)
        db.commit()

        legal_name_evidence = db.scalar(
            select(Evidence).where(Evidence.company_id == result.company_id, Evidence.field == "legal_name")
        )
        assert legal_name_evidence is not None
        assert legal_name_evidence.value == "SWIGGY LIMITED"

        obs = db.scalar(
            select(RawObservation).where(
                RawObservation.company_id == result.company_id, RawObservation.field == "legal_name"
            )
        )
        assert obs is not None
        assert obs.source_id == filesure_source.id
        assert obs.metadata_json["provider"] == "filesure"
        assert "underlying_source" in obs.metadata_json
