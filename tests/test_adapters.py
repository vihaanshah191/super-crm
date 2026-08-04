from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.source_adapters.base import FetchResult
from app.source_adapters.government_dataset_adapter import GovernmentDatasetAdapter
from app.source_adapters.website_adapter import WebsiteAdapter

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fetch_result(path: Path, content_type: str) -> FetchResult:
    return FetchResult(
        url=f"https://example.test/{path.name}",
        status_code=200,
        content=path.read_bytes(),
        content_type=content_type,
        fetched_at=datetime.now(timezone.utc),
    )


class TestWebsiteAdapter:
    @pytest.fixture()
    def fetch_result(self):
        return _fetch_result(FIXTURES_DIR / "html" / "example_company_website.html", "text/html")

    def test_parse_extracts_one_record(self, fetch_result):
        adapter = WebsiteAdapter(source_name="example_company_website")
        records = adapter.parse(fetch_result)
        assert len(records) == 1
        assert records[0].fields["canonical_name"] == "ABC Industries Pvt. Ltd."

    def test_parse_extracts_multi_value_products(self, fetch_result):
        adapter = WebsiteAdapter(source_name="example_company_website")
        record = adapter.parse(fetch_result)[0]
        assert record.fields["products"] == ["Industrial Solvents", "Specialty Polymers"]

    def test_normalize_produces_observed_verification_type(self, fetch_result):
        adapter = WebsiteAdapter(source_name="example_company_website")
        record = adapter.parse(fetch_result)[0]
        drafts = adapter.normalize(record)
        assert drafts
        assert all(d.verification_type == "observed" for d in drafts)

    def test_normalize_derives_postal_code_from_address(self, fetch_result):
        adapter = WebsiteAdapter(source_name="example_company_website")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d.normalized_value for d in adapter.normalize(record)}
        assert drafts["postal_code"] == "411019"

    def test_normalize_parses_employee_range(self, fetch_result):
        adapter = WebsiteAdapter(source_name="example_company_website")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d.normalized_value for d in adapter.normalize(record)}
        assert drafts["employee_range_min"] == "50"
        assert drafts["employee_range_max"] == "200"

    def test_parse_returns_empty_list_when_name_missing(self):
        fetch_result = FetchResult(
            url="https://example.test/empty.html",
            status_code=200,
            content=b"<html><body><p>No structured data here</p></body></html>",
            content_type="text/html",
            fetched_at=datetime.now(timezone.utc),
        )
        adapter = WebsiteAdapter(source_name="example_company_website")
        assert adapter.parse(fetch_result) == []


class TestGovernmentDatasetAdapter:
    @pytest.fixture()
    def fetch_result(self):
        return _fetch_result(FIXTURES_DIR / "data" / "mca_company_master_maharashtra.csv", "text/csv")

    def test_parse_extracts_two_records(self, fetch_result):
        adapter = GovernmentDatasetAdapter(source_name="mca_company_master_data")
        records = adapter.parse(fetch_result)
        assert len(records) == 2
        assert {r.external_ref for r in records} == {
            "U24100MH2015PTC123456",
            "U27200MH2018PTC234567",
        }

    def test_validate_rejects_malformed_cin(self, fetch_result):
        adapter = GovernmentDatasetAdapter(source_name="mca_company_master_data")
        record = adapter.parse(fetch_result)[0]
        bad_record = record.__class__(external_ref="short", fields={**record.fields, "cin": "short"})
        assert adapter.validate(bad_record) is False

    def test_normalize_produces_verified_verification_type(self, fetch_result):
        adapter = GovernmentDatasetAdapter(source_name="mca_company_master_data")
        record = adapter.parse(fetch_result)[0]
        drafts = adapter.normalize(record)
        assert drafts
        assert all(d.verification_type == "verified" for d in drafts)

    def test_normalize_produces_matching_normalized_name_across_sources(self, fetch_result):
        """The core entity-resolution precondition: MCA's legal name and the
        website's company name must normalize to the same comparison key."""
        from app.ingestion.normalization.company_name import normalize_company_name

        adapter = GovernmentDatasetAdapter(source_name="mca_company_master_data")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d.normalized_value for d in adapter.normalize(record)}
        assert drafts["canonical_name"] == normalize_company_name("ABC Industries Pvt. Ltd.")

    def test_normalize_parses_incorporation_date(self, fetch_result):
        adapter = GovernmentDatasetAdapter(source_name="mca_company_master_data")
        record = adapter.parse(fetch_result)[0]
        drafts = {d.field: d.normalized_value for d in adapter.normalize(record)}
        assert drafts["incorporation_date"] == "2015-04-12"

    def test_parse_skips_rows_without_cin(self):
        content = b"CIN,COMPANY_NAME\n,Missing CIN Co\n"
        fetch_result = FetchResult(
            url="https://example.test/bad.csv",
            status_code=200,
            content=content,
            content_type="text/csv",
            fetched_at=datetime.now(timezone.utc),
        )
        adapter = GovernmentDatasetAdapter(source_name="mca_company_master_data")
        assert adapter.parse(fetch_result) == []

    def test_parse_normalizes_column_names_with_efiling_suffix(self):
        """Real data.gov.in exports sometimes use 'COMPANY_STATUS(for efiling)'
        instead of 'COMPANY_STATUS'. The adapter must normalize the header."""
        content = (
            b"CIN,COMPANY_NAME,COMPANY_STATUS(for efiling),COMPANY_CLASS,COMPANY_CATEGORY,"
            b"AUTHORIZED_CAP,PAIDUP_CAPITAL,DATE_OF_REGISTRATION,REGISTERED_STATE,"
            b"REGISTERED_OFFICE_ADDRESS,ROC\n"
            b"U24100MH2015PTC123456,TEST CO PRIVATE LIMITED,Active,Private,"
            b"Company limited by Shares,1000000,1000000,2015-01-01,Maharashtra,"
            b"\"Pune, Maharashtra 411001\",RoC-Pune\n"
        )
        fetch_result = FetchResult(
            url="https://example.test/efiling.csv",
            status_code=200,
            content=content,
            content_type="text/csv",
            fetched_at=datetime.now(timezone.utc),
        )
        adapter = GovernmentDatasetAdapter(source_name="mca_company_master_data")
        records = adapter.parse(fetch_result)
        assert len(records) == 1
        assert records[0].external_ref == "U24100MH2015PTC123456"
        drafts = {d.field: d.normalized_value for d in adapter.normalize(records[0])}
        assert drafts.get("company_status") == "active"
        assert drafts.get("authorized_capital_inr") == "1000000"
