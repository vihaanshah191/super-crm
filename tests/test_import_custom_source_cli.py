"""Tests for app/cli/import_custom_source.py -- the generic counterpart to
tests/test_import_mca.py, using an admin-declared field mapping instead of
a hardcoded one."""

import argparse
import json

import pytest
from sqlalchemy import select

from app.cli.import_custom_source import _execute, _ImportAborted
from app.models.company import Company
from app.models.ingestion_job import IngestionJob
from app.models.source import Source

CSV_CONTENT = (
    "Company Name,CIN Number,State,Turnover\n"
    "Acme Widgets Pvt Ltd,U12345MH2015PTC000111,Maharashtra,50000000\n"
    "Beta Traders,,Gujarat,20000000\n"
)

MAPPING = {"Company Name": "legal_name", "CIN Number": "cin", "State": "state", "Turnover": "annual_revenue_inr"}


@pytest.fixture()
def csv_file(tmp_path):
    path = tmp_path / "leads.csv"
    path.write_text(CSV_CONTENT)
    return path


def _args(**overrides) -> argparse.Namespace:
    defaults = dict(
        file=None,
        source_name="custom_cli_test_source",
        mapping=json.dumps(MAPPING),
        mapping_file=None,
        declared_origin="Test fixture",
        dry_run=True,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestDryRun:
    def test_dry_run_writes_nothing_but_source_bootstrap(self, db, csv_file):
        stats = _execute(_args(file=str(csv_file)))
        assert stats.rows_read == 2
        # Both rows have a legal_name (validate()'s only requirement) even
        # though "Beta Traders" has no CIN -- CIN just isn't one of its
        # observations, the row itself is still valid.
        assert stats.valid_rows == 2
        assert db.query(Company).count() == 0

    def test_source_row_is_created_even_in_dry_run(self, db, csv_file):
        _execute(_args(file=str(csv_file)))
        source = db.scalar(select(Source).where(Source.name == "custom_cli_test_source"))
        assert source is not None
        assert source.metadata_json["field_mapping"] == MAPPING

    def test_no_ingestion_job_committed_in_dry_run(self, db, csv_file):
        _execute(_args(file=str(csv_file)))
        assert db.query(IngestionJob).count() == 0


class TestRealRun:
    def test_commits_companies_and_job(self, db, csv_file):
        stats = _execute(_args(file=str(csv_file), dry_run=False))
        assert stats.new_companies == 2
        assert db.query(Company).count() == 2
        job = db.scalar(select(IngestionJob))
        assert job.status == "success"
        assert job.records_updated == 2

    def test_second_run_with_same_cin_matches_not_duplicates(self, db, csv_file):
        _execute(_args(file=str(csv_file), dry_run=False))
        _execute(_args(file=str(csv_file), dry_run=False))
        assert db.query(Company).count() == 2  # not 4


class TestMappingValidation:
    def test_missing_name_field_refuses_to_run(self, db, csv_file):
        bad_mapping = {"State": "state"}
        with pytest.raises(_ImportAborted):
            _execute(_args(file=str(csv_file), mapping=json.dumps(bad_mapping)))
        assert db.query(Company).count() == 0

    def test_unknown_canonical_field_refuses_to_run(self, db, csv_file):
        bad_mapping = {"Company Name": "legal_name", "Foo": "not_a_real_field"}
        with pytest.raises(_ImportAborted):
            _execute(_args(file=str(csv_file), mapping=json.dumps(bad_mapping)))

    def test_missing_mapping_argument_refuses_to_run(self, db, csv_file):
        with pytest.raises(_ImportAborted):
            _execute(_args(file=str(csv_file), mapping=None, mapping_file=None))

    def test_malformed_json_mapping_refuses_to_run(self, db, csv_file):
        with pytest.raises(_ImportAborted):
            _execute(_args(file=str(csv_file), mapping="{not valid json"))


class TestFileHandling:
    def test_missing_file_returns_error_exit_code(self, db):
        from app.cli.import_custom_source import run

        exit_code = run(_args(file="/nonexistent/path.csv"))
        assert exit_code == 1

    def test_reruns_reuse_and_update_the_source_row(self, db, csv_file, tmp_path):
        _execute(_args(file=str(csv_file)))
        new_mapping = {**MAPPING, "Extra": "industry"}
        # Second run declares a different mapping for the same source name --
        # should overwrite, not create a duplicate Source row.
        row2 = tmp_path / "leads2.csv"
        row2.write_text("Company Name,CIN Number,State,Turnover,Extra\nX Co,,MH,1,Chemicals\n")
        _execute(_args(file=str(row2), mapping=json.dumps(new_mapping)))

        sources = list(db.scalars(select(Source).where(Source.name == "custom_cli_test_source")))
        assert len(sources) == 1
        assert sources[0].metadata_json["field_mapping"] == new_mapping
