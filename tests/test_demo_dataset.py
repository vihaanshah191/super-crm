"""Tests for the client-demo dataset: generation (app.cli.generate_demo_dataset)
and seeding through the real ingestion pipeline (app.cli.seed_demo)."""

from sqlalchemy import select

from app.cli.generate_demo_dataset import FIELD_MAPPING, generate_rows
from app.cli.seed_demo import DEMO_SAVED_SEARCH_CREATED_BY, DEMO_SOURCE_NAME, run as run_seed_demo
from app.models.company import Company
from app.models.saved_search import SavedSearch
from app.models.source import Source
from app.source_adapters.custom_field_mapping import validate_field_mapping


class TestGenerateRows:
    def test_deterministic_for_same_seed(self):
        assert generate_rows(count=50, seed=7) == generate_rows(count=50, seed=7)

    def test_different_seed_produces_different_data(self):
        assert generate_rows(count=50, seed=7) != generate_rows(count=50, seed=8)

    def test_row_count_matches_request(self):
        assert len(generate_rows(count=123, seed=1)) == 123

    def test_field_mapping_is_valid_against_the_real_custom_source_validator(self):
        issues = validate_field_mapping(FIELD_MAPPING)
        assert [i for i in issues if i.severity == "error"] == []

    def test_cins_are_globally_unique(self):
        rows = generate_rows(count=500, seed=3)
        cins = [r["CIN"] for r in rows]
        assert len(cins) == len(set(cins))

    def test_company_names_are_not_dominated_by_a_single_shared_prefix(self):
        """Regression: an earlier generator used "{prefix} {suffix} {seq}"
        which put ~20% of a 750-row batch into the entity-resolution review
        queue (fuzzy name-similarity matched against other rows sharing the
        same prefix+suffix, differing only by the trailing number). Real
        names need enough entropy that two different rows rarely look like
        a probable duplicate of each other."""
        rows = generate_rows(count=500, seed=3)
        names = [r["Company Name"] for r in rows]
        assert len(set(names)) > 0.95 * len(names)

    def test_all_five_employee_revenue_shapes_appear(self):
        """DEFINITE/POSSIBLE/UNKNOWN demonstration depends on all five
        employee/revenue data shapes actually showing up in a realistically
        sized batch -- see the module docstring on generate_demo_dataset."""
        rows = generate_rows(count=500, seed=11)

        def shape(r: dict) -> str:
            has_emp_exact = bool(r["Employees"])
            has_emp_range = bool(r["Employee Range Min"])
            has_rev_exact = bool(r["Annual Revenue (INR)"])
            has_rev_range = bool(r["Revenue Range Min (INR)"])
            if has_emp_exact and has_rev_exact:
                return "both_exact"
            if has_emp_range and has_rev_range:
                return "both_range"
            if not has_emp_exact and not has_emp_range and has_rev_exact:
                return "employees_unknown"
            if has_emp_exact and not has_rev_exact and not has_rev_range:
                return "revenue_unknown"
            if not any([has_emp_exact, has_emp_range, has_rev_exact, has_rev_range]):
                return "both_unknown"
            return "other"

        shapes = {shape(r) for r in rows}
        assert shapes == {"both_exact", "both_range", "employees_unknown", "revenue_unknown", "both_unknown"}

    def test_flagship_query_combination_has_a_healthy_number_of_candidates(self):
        """Maharashtra + Manufacturing is the example client query -- the
        generator weights state/industry selection so this isn't a handful
        of coincidental matches in a demo-sized batch."""
        rows = generate_rows(count=500, seed=42)
        matches = [r for r in rows if r["State"] == "Maharashtra" and r["Industry"] == "Manufacturing"]
        assert len(matches) >= 5


class TestSeedDemo:
    def test_seed_demo_creates_a_clearly_labeled_demo_source(self, db):
        run_seed_demo(count=25, seed=1, reset=False)
        source = db.scalar(select(Source).where(Source.name == DEMO_SOURCE_NAME))
        assert source is not None
        assert source.display_name == "Super CRM Demo Dataset"
        assert source.countries == ["IN"]
        assert "SYNTHETIC" in (source.license_notes or "").upper()

    def test_seed_demo_creates_searchable_companies(self, db):
        run_seed_demo(count=25, seed=1, reset=False)
        count = db.scalar(select(Company).limit(1))
        assert count is not None
        total = len(list(db.scalars(select(Company))))
        assert total > 0

    def test_seed_demo_creates_the_four_saved_searches(self, db):
        run_seed_demo(count=25, seed=1, reset=False)
        names = {
            s.name
            for s in db.scalars(select(SavedSearch).where(SavedSearch.created_by == DEMO_SAVED_SEARCH_CREATED_BY))
        }
        assert names == {
            "Maharashtra Manufacturers",
            "Large Maharashtra Manufacturers",
            "High Revenue Indian Companies",
            "Export Manufacturers",
        }

    def test_saved_searches_use_the_frontends_created_by_placeholder(self):
        """Regression: the Discover page's saved-searches list is filtered
        by created_by (CREATED_BY constant in frontend/src/app/discover/
        page.tsx = "frontend-operator"). Seeding demo saved searches under
        any other created_by makes them exist in the database but never
        appear in the UI -- caught by an actual screenshot of the demo, not
        by reading the code."""
        assert DEMO_SAVED_SEARCH_CREATED_BY == "frontend-operator"

    def test_export_status_and_sub_industry_project_onto_company(self, db):
        """Regression: _apply_field_to_company() had no case for
        export_status/sub_industry/company_category -- custom-source values
        for these were recorded as Evidence but silently never applied to
        the canonical Company row, which would have broken the 'Export
        Manufacturers' demo saved search entirely."""
        run_seed_demo(count=60, seed=2, reset=False)
        companies = list(db.scalars(select(Company)))
        assert any(c.export_status is not None for c in companies)
        assert any(c.sub_industry is not None for c in companies)

    def test_reset_removes_previously_imported_demo_data_before_reimporting(self, db):
        run_seed_demo(count=20, seed=5, reset=False)
        first_count = len(list(db.scalars(select(Company))))
        assert first_count > 0

        run_seed_demo(count=20, seed=5, reset=True)
        second_count = len(list(db.scalars(select(Company))))
        # Re-seeding with --reset shouldn't accumulate duplicate companies
        # from the first run on top of the second.
        assert second_count <= first_count * 1.2

        saved_search_count = len(
            list(db.scalars(select(SavedSearch).where(SavedSearch.created_by == DEMO_SAVED_SEARCH_CREATED_BY)))
        )
        assert saved_search_count == 4
