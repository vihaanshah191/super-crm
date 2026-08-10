"""Tests for app/cli/import_mca.py against tests/fixtures/data/mca_*.csv.

These fixtures are test data ONLY -- they are not evidence that the live
data.gov.in MCA schema matches; see docs/mca_data_access.md.
"""

import argparse
from pathlib import Path

import pytest
from sqlalchemy import select

from app.cli.import_mca import FILE_IMPORT_SOURCE_NAME, _execute
from app.models.company import Company
from app.models.ingestion_job import IngestionJob
from app.models.observation import RawObservation
from app.models.source import Source

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "data"


def _args(**overrides) -> argparse.Namespace:
    defaults = dict(
        file=str(FIXTURES_DIR / "mca_comprehensive_sample.csv"),
        source_url="https://www.data.gov.in/catalog/company-master-data",
        license="Government Open Data License - India (GODL)",
        dataset_name="MCA Company Master Data",
        dataset_publication_date=None,
        limit=None,
        offset=0,
        dry_run=True,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestDryRunStats:
    """Expected counts against mca_comprehensive_sample.csv (11 data rows):
    row1 normal, row2 missing CIN, row3 malformed CIN, row4 duplicate of
    row1's CIN, row5 inactive (Strike Off), row6/7 different states+category,
    row8 zero capital, row9 large capital, row10 malformed capital, row11
    missing incorporation date/state."""

    def test_rows_read_matches_file_row_count(self, db):
        stats = _execute(_args())
        assert stats.rows_read == 11

    def test_missing_and_malformed_cin_are_distinguished(self, db):
        stats = _execute(_args())
        assert stats.missing_cin == 1
        assert stats.malformed_cin == 1
        assert stats.invalid_rows == 2

    def test_valid_rows_excludes_missing_and_malformed_cin(self, db):
        stats = _execute(_args())
        assert stats.valid_rows == 9

    def test_duplicate_cin_is_detected(self, db):
        stats = _execute(_args())
        assert stats.duplicate_cins == 1

    def test_missing_optional_fields_are_counted(self, db):
        stats = _execute(_args())
        assert stats.missing_incorporation_date == 1
        assert stats.missing_state == 1

    def test_malformed_monetary_value_is_counted_zero_and_large_are_not(self, db):
        stats = _execute(_args())
        # Only row10 ("N.A.") is malformed; row8 (0) and row9 (large) are valid numbers.
        assert stats.malformed_monetary_values == 1

    def test_new_vs_existing_companies_reflects_cin_entity_resolution(self, db):
        stats = _execute(_args())
        # 8 distinct new CINs (rows 1,5,6,7,8,9,10,11) + row4 auto-matches row1's CIN.
        assert stats.new_companies == 8
        assert stats.existing_companies == 1
        assert stats.ambiguous_matches == 0

    def test_unknown_column_does_not_crash_or_appear_as_a_field(self, db):
        stats = _execute(_args())
        assert stats.parsing_failures == 0


class TestDryRunDoesNotWriteCompanyData:
    def test_dry_run_leaves_no_companies(self, db):
        _execute(_args(dry_run=True))
        assert db.scalar(select(Company).limit(1)) is None

    def test_dry_run_leaves_no_raw_observations(self, db):
        _execute(_args(dry_run=True))
        assert db.scalar(select(RawObservation).limit(1)) is None

    def test_dry_run_leaves_no_ingestion_job(self, db):
        _execute(_args(dry_run=True))
        assert db.scalar(select(IngestionJob).limit(1)) is None

    def test_dry_run_still_bootstraps_the_source_registry_row(self, db):
        """Source-row bootstrap is registry metadata, not company data -- it's
        allowed to persist even under --dry-run (see _get_or_create_file_import_source)."""
        _execute(_args(dry_run=True))
        assert db.scalar(select(Source).where(Source.name == FILE_IMPORT_SOURCE_NAME)) is not None


class TestRealImportWritesData:
    def test_real_import_creates_expected_company_count(self, db):
        stats = _execute(_args(dry_run=False))
        companies = list(db.scalars(select(Company)))
        assert len(companies) == 8  # 8 distinct CINs, row4 merges into row1's company
        assert stats.new_companies == 8

    def test_real_import_creates_an_ingestion_job(self, db):
        _execute(_args(dry_run=False))
        job = db.scalar(select(IngestionJob))
        assert job is not None
        assert job.status in ("success", "partial")

    def test_observations_carry_file_provenance_metadata(self, db):
        _execute(_args(dry_run=False))
        obs = db.scalar(select(RawObservation))
        assert obs.metadata_json["import_provenance_status"] == "file_import_user_declared"
        assert obs.metadata_json["original_filename"] == "mca_comprehensive_sample.csv"
        assert len(obs.metadata_json["file_sha256"]) == 64
        assert obs.metadata_json["official_source_url"] == "https://www.data.gov.in/catalog/company-master-data"

    def test_authorized_capital_never_populates_revenue_fields(self, db):
        """Explicit product requirement: authorized/paid-up capital must never
        be projected onto Company.annual_revenue_inr or revenue_range_*."""
        _execute(_args(dry_run=False))
        companies = list(db.scalars(select(Company)))
        for c in companies:
            assert c.annual_revenue_inr is None
            assert c.revenue_range_min_inr is None
            assert c.revenue_range_max_inr is None

    def test_roc_is_preserved_as_evidence(self, db):
        from app.models.evidence import Evidence

        _execute(_args(dry_run=False))
        company = db.scalar(select(Company).where(Company.cin == "U24100MH2015PTC100001"))
        roc_evidence = db.scalar(select(Evidence).where(Evidence.company_id == company.id, Evidence.field == "roc"))
        assert roc_evidence is not None
        assert roc_evidence.value == "RoC-Pune"


class TestLimitAndOffset:
    def test_limit_restricts_window(self, db):
        stats = _execute(_args(limit=1, offset=0))
        assert stats.rows_read == 1

    def test_offset_skips_rows(self, db):
        # Row at offset 1 is the "missing CIN" row.
        stats = _execute(_args(limit=1, offset=1))
        assert stats.rows_read == 1
        assert stats.missing_cin == 1


class TestAlternateColumnNames:
    def test_alternate_headers_are_mapped_and_ingested(self, db):
        stats = _execute(
            _args(file=str(FIXTURES_DIR / "mca_alternate_columns.csv"), dry_run=False)
        )
        assert stats.rows_read == 1
        assert stats.new_companies == 1
        company = db.scalar(select(Company).where(Company.cin == "U24100RJ2014PTC200001"))
        assert company is not None
        assert company.state == "Rajasthan"


class TestSourceUrlRequired:
    def test_missing_file_reports_error_without_traceback(self, db, capsys):
        from app.cli.import_mca import run

        exit_code = run(_args(file="/nonexistent/path.csv"))
        assert exit_code == 1
