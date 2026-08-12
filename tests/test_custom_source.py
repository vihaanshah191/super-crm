from datetime import datetime, timezone

import pytest

from app.models.enums import VerificationType
from app.source_adapters.base import FetchResult
from app.source_adapters.custom_field_mapping import (
    CANONICAL_FIELD_TYPES,
    map_row,
    validate_field_mapping,
    value_matches_type,
)
from app.source_adapters.custom_file_adapter import CustomFileAdapter


class TestValidateFieldMapping:
    def test_empty_mapping_is_an_error(self):
        issues = validate_field_mapping({})
        assert any(i.severity == "error" for i in issues)

    def test_unknown_canonical_field_is_an_error(self):
        issues = validate_field_mapping({"Company Name": "legal_name", "Foo": "not_a_real_field"})
        assert any(i.severity == "error" and "not_a_real_field" in i.message for i in issues)

    def test_missing_name_field_is_an_error(self):
        issues = validate_field_mapping({"State": "state"})
        assert any(i.severity == "error" and "legal_name" in i.message for i in issues)

    def test_duplicate_canonical_target_is_a_warning_not_an_error(self):
        issues = validate_field_mapping({"Company Name": "legal_name", "Name": "legal_name"})
        assert any(i.severity == "warning" for i in issues)
        assert not any(i.severity == "error" for i in issues)

    def test_valid_mapping_has_no_errors(self):
        issues = validate_field_mapping(
            {"Company Name": "legal_name", "CIN Number": "cin", "State": "state", "Turnover": "annual_revenue_inr"}
        )
        assert not any(i.severity == "error" for i in issues)


class TestValueMatchesType:
    def test_empty_value_always_matches(self):
        assert value_matches_type("", "number") is True
        assert value_matches_type("   ", "date") is True

    def test_number_type(self):
        assert value_matches_type("12,345.50", "number") is True
        assert value_matches_type("not a number", "number") is False

    def test_date_type(self):
        assert value_matches_type("2020-01-15", "date") is True
        assert value_matches_type("not a date", "date") is False

    def test_boolean_type(self):
        assert value_matches_type("yes", "boolean") is True
        assert value_matches_type("maybe", "boolean") is False

    def test_string_type_accepts_anything(self):
        assert value_matches_type("literally anything", "string") is True


class TestMapRow:
    def test_maps_known_fields_and_drops_unmapped(self):
        row = {"Company Name": "Acme Ltd", "Random Column": "ignored"}
        mapping = {"Company Name": "legal_name"}
        mapped = map_row(row, mapping)
        assert mapped == {"legal_name": "Acme Ltd"}

    def test_drops_unknown_canonical_targets(self):
        row = {"X": "value"}
        mapping = {"X": "not_a_real_canonical_field"}
        assert map_row(row, mapping) == {}


CSV_CONTENT = (
    "Company Name,CIN Number,State,Turnover,Founded\n"
    "Acme Widgets Pvt Ltd,U12345MH2015PTC000111,Maharashtra,50000000,2015-06-01\n"
    "No Name Row,,Gujarat,,\n"  # missing legal_name -> should be dropped/invalid
    "Bad Turnover Co,U99999KA2018PTC000222,Karnataka,not-a-number,2018-01-01\n"
)

MAPPING = {"Company Name": "legal_name", "CIN Number": "cin", "State": "state", "Turnover": "annual_revenue_inr"}


def _fetch_result(content: str, content_type: str = "text/csv") -> FetchResult:
    return FetchResult(
        url="file:///tmp/test.csv",
        status_code=200,
        content=content.encode("utf-8"),
        content_type=content_type,
        fetched_at=datetime.now(timezone.utc),
    )


class TestCustomFileAdapterParse:
    def test_parses_csv_rows(self):
        adapter = CustomFileAdapter(source_name="test_custom", field_mapping=MAPPING)
        records = adapter.parse(_fetch_result(CSV_CONTENT))
        assert len(records) == 3  # all rows produce a record; validate() filters the no-name one later

    def test_external_ref_prefers_cin(self):
        adapter = CustomFileAdapter(source_name="test_custom", field_mapping=MAPPING)
        records = adapter.parse(_fetch_result(CSV_CONTENT))
        acme = next(r for r in records if r.fields.get("legal_name") == "Acme Widgets Pvt Ltd")
        assert acme.external_ref == "U12345MH2015PTC000111"

    def test_row_with_no_mapped_fields_at_all_is_dropped(self):
        adapter = CustomFileAdapter(source_name="test_custom", field_mapping={"Unmapped": "not_a_real_field"})
        records = adapter.parse(_fetch_result(CSV_CONTENT))
        assert records == []

    def test_parses_json_array(self):
        import json

        rows = [{"Company Name": "JSON Co", "CIN Number": "U11111DL2020PTC000333", "State": "Delhi"}]
        adapter = CustomFileAdapter(source_name="test_custom", field_mapping=MAPPING)
        records = adapter.parse(_fetch_result(json.dumps(rows), content_type="application/json"))
        assert len(records) == 1
        assert records[0].fields["legal_name"] == "JSON Co"


class TestCustomFileAdapterValidate:
    def test_requires_legal_name(self):
        adapter = CustomFileAdapter(source_name="test_custom", field_mapping=MAPPING)
        records = adapter.parse(_fetch_result(CSV_CONTENT))
        results = {r.fields.get("legal_name", ""): adapter.validate(r) for r in records}
        assert results["Acme Widgets Pvt Ltd"] is True
        assert results.get("", False) is False


class TestCustomFileAdapterNormalize:
    def test_produces_observed_verification_type_and_below_registry_confidence(self):
        adapter = CustomFileAdapter(source_name="test_custom", field_mapping=MAPPING)
        records = adapter.parse(_fetch_result(CSV_CONTENT))
        acme = next(r for r in records if r.fields.get("legal_name") == "Acme Widgets Pvt Ltd")
        drafts = adapter.normalize(acme)
        assert drafts
        for d in drafts:
            assert d.verification_type == VerificationType.OBSERVED.value
            assert d.confidence < 0.5  # below every registry-backed source

    def test_derives_canonical_name(self):
        adapter = CustomFileAdapter(source_name="test_custom", field_mapping=MAPPING)
        records = adapter.parse(_fetch_result(CSV_CONTENT))
        acme = next(r for r in records if r.fields.get("legal_name") == "Acme Widgets Pvt Ltd")
        drafts = {d.field: d for d in adapter.normalize(acme)}
        assert "canonical_name" in drafts

    def test_malformed_numeric_cell_is_skipped_not_whole_row(self):
        adapter = CustomFileAdapter(source_name="test_custom", field_mapping=MAPPING)
        records = adapter.parse(_fetch_result(CSV_CONTENT))
        bad_turnover = next(r for r in records if r.fields.get("legal_name") == "Bad Turnover Co")
        drafts = {d.field: d for d in adapter.normalize(bad_turnover)}
        assert "annual_revenue_inr" not in drafts  # malformed -- dropped
        assert "legal_name" in drafts  # rest of the row still processed
        assert "cin" in drafts

    def test_capital_fields_never_produce_revenue(self):
        # Sanity check specific to this project's "never conflate capital
        # with revenue" rule -- CANONICAL_FIELD_TYPES has no capital fields
        # at all for custom sources, so there's nothing to accidentally map.
        assert "authorized_capital_inr" not in CANONICAL_FIELD_TYPES
        assert "paidup_capital_inr" not in CANONICAL_FIELD_TYPES


class TestCustomSourceEntityResolution:
    def test_ingests_new_company_through_full_pipeline(self, db):
        from app.compliance.source_policy import SourcePolicy
        from app.ingestion.pipeline import ingest_parsed_record
        from app.models.source import Source

        source = Source(
            name="custom_test_source",
            source_type="user_file",
            collection_enabled=True,
            reliability_weight=30,
            license_notes="test",
        )
        db.add(source)
        db.commit()

        adapter = CustomFileAdapter(source_name=source.name, field_mapping=MAPPING)
        records = adapter.parse(_fetch_result(CSV_CONTENT))
        acme = next(r for r in records if r.fields.get("legal_name") == "Acme Widgets Pvt Ltd")

        policy = SourcePolicy(
            source_name=source.name,
            collection_enabled=source.collection_enabled,
            rate_limit_per_minute=source.rate_limit_per_minute,
            max_concurrency=source.max_concurrency,
        )
        result = ingest_parsed_record(db, adapter, source, policy, acme)
        assert result.decision == "new_company"
        assert result.company_id is not None

    def test_country_field_projects_onto_company_country_column(self, db):
        """Company.country is a real, pre-existing column, but
        pipeline.py's _apply_field_to_company() had no case for it before
        this change -- a 'country' observation was recorded as Evidence
        but silently never applied to the canonical row. Fixed as part of
        Phase 7 since a custom source is the first adapter likely to map a
        country column explicitly."""
        from app.compliance.source_policy import SourcePolicy
        from app.ingestion.pipeline import ingest_parsed_record
        from app.models.company import Company
        from app.models.source import Source

        source = Source(
            name="custom_country_test_source",
            source_type="user_file",
            collection_enabled=True,
            reliability_weight=30,
        )
        db.add(source)
        db.commit()

        mapping = {"Company Name": "legal_name", "Country": "country"}
        adapter = CustomFileAdapter(source_name=source.name, field_mapping=mapping)
        fetch_result = _fetch_result("Company Name,Country\nGlobex Inc,United States\n")
        record = adapter.parse(fetch_result)[0]

        policy = SourcePolicy(
            source_name=source.name,
            collection_enabled=source.collection_enabled,
            rate_limit_per_minute=source.rate_limit_per_minute,
            max_concurrency=source.max_concurrency,
        )
        result = ingest_parsed_record(db, adapter, source, policy, record)
        company = db.get(Company, result.company_id)
        assert company.country == "United States"

    def test_country_code_field_projects_onto_company_country_code_column(self, db):
        """Company.country_code backs country_scope-restricted saved
        searches (app.search.advanced_query), but before this change no
        ingestion path -- including a custom source explicitly mapping a
        country-code column -- ever set it. Mirrors the 'country' test
        above."""
        from app.compliance.source_policy import SourcePolicy
        from app.ingestion.pipeline import ingest_parsed_record
        from app.models.company import Company
        from app.models.source import Source

        source = Source(
            name="custom_country_code_test_source",
            source_type="user_file",
            collection_enabled=True,
            reliability_weight=30,
        )
        db.add(source)
        db.commit()

        mapping = {"Company Name": "legal_name", "ISO": "country_code"}
        adapter = CustomFileAdapter(source_name=source.name, field_mapping=mapping)
        fetch_result = _fetch_result("Company Name,ISO\nGlobex Inc,us\n")
        record = adapter.parse(fetch_result)[0]

        policy = SourcePolicy(
            source_name=source.name,
            collection_enabled=source.collection_enabled,
            rate_limit_per_minute=source.rate_limit_per_minute,
            max_concurrency=source.max_concurrency,
        )
        result = ingest_parsed_record(db, adapter, source, policy, record)
        company = db.get(Company, result.company_id)
        assert company.country_code == "US"

    def test_revenue_fields_project_onto_company_columns(self, db):
        """CANONICAL_FIELD_TYPES lets a custom source map a column to
        annual_revenue_inr/revenue_range_min_inr/revenue_range_max_inr/
        revenue_year (used in the CLI examples/docs), but
        _apply_field_to_company() had no case for any of them -- the values
        were recorded as Evidence but silently never applied to the
        canonical row, making them invisible to search/sort. Mirrors
        test_country_field_projects_onto_company_country_column above."""
        from app.compliance.source_policy import SourcePolicy
        from app.ingestion.pipeline import ingest_parsed_record
        from app.models.company import Company
        from app.models.source import Source

        source = Source(
            name="custom_revenue_test_source",
            source_type="user_file",
            collection_enabled=True,
            reliability_weight=30,
        )
        db.add(source)
        db.commit()

        mapping = {
            "Company Name": "legal_name",
            "Turnover": "annual_revenue_inr",
            "Turnover Min": "revenue_range_min_inr",
            "Turnover Max": "revenue_range_max_inr",
            "Turnover Year": "revenue_year",
        }
        adapter = CustomFileAdapter(source_name=source.name, field_mapping=mapping)
        fetch_result = _fetch_result(
            "Company Name,Turnover,Turnover Min,Turnover Max,Turnover Year\n"
            "Acme Widgets Pvt Ltd,150000000,100000000,200000000,2024\n"
        )
        record = adapter.parse(fetch_result)[0]

        policy = SourcePolicy(
            source_name=source.name,
            collection_enabled=source.collection_enabled,
            rate_limit_per_minute=source.rate_limit_per_minute,
            max_concurrency=source.max_concurrency,
        )
        result = ingest_parsed_record(db, adapter, source, policy, record)
        company = db.get(Company, result.company_id)
        assert company.annual_revenue_inr == 150000000
        assert company.revenue_range_min_inr == 100000000
        assert company.revenue_range_max_inr == 200000000
        assert company.revenue_year == 2024

    def test_matches_existing_company_by_cin_across_sources(self, db, mca_source):
        """A company already known via MCA (VERIFIED, high confidence) should
        resolve to the SAME Company row when the same CIN later shows up in
        a low-confidence custom file -- proving cross-source entity
        resolution, not just within-source dedup."""
        from app.compliance.source_policy import SourcePolicy
        from app.ingestion.pipeline import ingest_parsed_record
        from app.models.company import Company
        from app.models.source import Source

        existing = Company(
            canonical_name="acme widgets",
            normalized_name="acme widgets",
            cin="U12345MH2015PTC000111",
            confidence=0.95,
        )
        db.add(existing)
        db.commit()

        custom_source = Source(
            name="custom_test_source_2", source_type="user_file", collection_enabled=True, reliability_weight=30
        )
        db.add(custom_source)
        db.commit()

        adapter = CustomFileAdapter(source_name=custom_source.name, field_mapping=MAPPING)
        records = adapter.parse(_fetch_result(CSV_CONTENT))
        acme = next(r for r in records if r.fields.get("legal_name") == "Acme Widgets Pvt Ltd")

        policy = SourcePolicy(
            source_name=custom_source.name,
            collection_enabled=custom_source.collection_enabled,
            rate_limit_per_minute=custom_source.rate_limit_per_minute,
            max_concurrency=custom_source.max_concurrency,
        )
        result = ingest_parsed_record(db, adapter, custom_source, policy, acme)
        assert result.decision == "auto_match"
        assert result.company_id == existing.id
