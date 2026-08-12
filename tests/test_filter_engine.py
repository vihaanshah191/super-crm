import pytest
from pydantic import ValidationError

from app.models.company import Company
from app.search.advanced_query import find_unknown_bucket, search_companies_advanced
from app.search.filter_types import (
    FilterCondition,
    FilterDataType,
    FilterGroup,
    FilterOperator,
    MatchStrength,
    SortDirection,
    SortSpec,
    UnknownHandling,
)


def _company(**overrides) -> Company:
    defaults = dict(
        canonical_name="ABC Industries",
        normalized_name="abc industries",
        state="Maharashtra",
        city="Pune",
        country="India",
        industry="Manufacturing",
        company_category="manufacturer",
        export_status=True,
        employee_range_min=50,
        employee_range_max=200,
        annual_revenue_inr=150_000_000,
        confidence=0.9,
    )
    defaults.update(overrides)
    return Company(**defaults)


def cond(field, operator, value=None, data_type=FilterDataType.STRING) -> FilterCondition:
    return FilterCondition(field=field, operator=operator, value=value, data_type=data_type)


class TestOperatorValidationPerDataType:
    def test_contains_rejected_for_date_field(self):
        with pytest.raises(ValidationError):
            FilterCondition(
                field="incorporation_date",
                operator=FilterOperator.CONTAINS,
                value="2020",
                data_type=FilterDataType.DATE,
            )

    def test_gt_rejected_for_boolean_field(self):
        with pytest.raises(ValidationError):
            FilterCondition(
                field="export_status", operator=FilterOperator.GT, value=True, data_type=FilterDataType.BOOLEAN
            )

    def test_exists_rejects_a_value(self):
        with pytest.raises(ValidationError):
            FilterCondition(
                field="website", operator=FilterOperator.EXISTS, value="x", data_type=FilterDataType.STRING
            )

    def test_between_requires_two_element_list(self):
        with pytest.raises(ValidationError):
            FilterCondition(
                field="employees", operator=FilterOperator.BETWEEN, value=[5], data_type=FilterDataType.NUMBER
            )

    def test_in_requires_nonempty_list(self):
        with pytest.raises(ValidationError):
            FilterCondition(
                field="state", operator=FilterOperator.IN, value=[], data_type=FilterDataType.STRING
            )

    def test_valid_condition_constructs(self):
        c = cond("state", FilterOperator.EQ, "Maharashtra")
        assert c.field == "state"


class TestStringFilters:
    def test_eq_is_case_insensitive(self, db):
        match = _company(state="Maharashtra")
        other = _company(canonical_name="Other", normalized_name="other", state="Gujarat")
        db.add_all([match, other])
        db.commit()

        results = search_companies_advanced(db, cond("state", FilterOperator.EQ, "maharashtra"))
        ids = {r.company.id for r in results}
        assert match.id in ids
        assert other.id not in ids

    def test_contains(self, db):
        match = _company(industry="Chemical Manufacturing")
        db.add(match)
        db.commit()

        results = search_companies_advanced(db, cond("industry", FilterOperator.CONTAINS, "chemical"))
        assert match.id in {r.company.id for r in results}

    def test_in_operator(self, db):
        mh = _company(state="Maharashtra")
        gj = _company(canonical_name="GJ", normalized_name="gj", state="Gujarat")
        ka = _company(canonical_name="KA", normalized_name="ka", state="Karnataka")
        db.add_all([mh, gj, ka])
        db.commit()

        results = search_companies_advanced(
            db, cond("state", FilterOperator.IN, ["Maharashtra", "Gujarat"])
        )
        ids = {r.company.id for r in results}
        assert mh.id in ids and gj.id in ids
        assert ka.id not in ids

    def test_exists_excludes_null_website(self, db):
        has_site = _company(website_domain="example.com")
        no_site = _company(canonical_name="No Site", normalized_name="no site", website_domain=None)
        db.add_all([has_site, no_site])
        db.commit()

        results = search_companies_advanced(db, cond("website", FilterOperator.EXISTS))
        ids = {r.company.id for r in results}
        assert has_site.id in ids
        assert no_site.id not in ids


class TestCountryCodeFilter:
    def test_filters_by_iso_country_code(self, db):
        india = _company(country="India", country_code="IN")
        usa = _company(canonical_name="US Co", normalized_name="us co", country="United States", country_code="US")
        db.add_all([india, usa])
        db.commit()

        results = search_companies_advanced(db, cond("country_code", FilterOperator.EQ, "IN"))
        ids = {r.company.id for r in results}
        assert india.id in ids
        assert usa.id not in ids

    def test_unset_country_code_is_unknown_not_excluded_as_zero(self, db):
        # A pre-Migration-2 company with a free-text country but no
        # country_code yet -- filtering on country_code must never treat
        # this as "definitely not IN", only as unknown.
        legacy = _company(country="India", country_code=None)
        db.add(legacy)
        db.commit()

        main_results = search_companies_advanced(db, cond("country_code", FilterOperator.EQ, "IN"))
        assert legacy.id not in {r.company.id for r in main_results}

        unknown = find_unknown_bucket(db, cond("country_code", FilterOperator.EQ, "IN"))
        assert legacy.id in {c.id for c in unknown}


class TestEnumFilter:
    def test_eq_is_case_sensitive_exact(self, db):
        match = _company(company_category="manufacturer")
        other = _company(canonical_name="Other", normalized_name="other", company_category="distributor")
        db.add_all([match, other])
        db.commit()

        results = search_companies_advanced(
            db, cond("company_category", FilterOperator.EQ, "manufacturer", FilterDataType.ENUM)
        )
        ids = {r.company.id for r in results}
        assert match.id in ids
        assert other.id not in ids


class TestBooleanFilter:
    def test_eq_true(self, db):
        exporter = _company(export_status=True)
        non_exporter = _company(canonical_name="Non", normalized_name="non", export_status=False)
        db.add_all([exporter, non_exporter])
        db.commit()

        results = search_companies_advanced(
            db, cond("export_status", FilterOperator.EQ, True, FilterDataType.BOOLEAN)
        )
        ids = {r.company.id for r in results}
        assert exporter.id in ids
        assert non_exporter.id not in ids


class TestDateFilter:
    def test_gt(self, db):
        from datetime import date

        new_co = _company(incorporation_date=date(2020, 1, 1))
        old_co = _company(canonical_name="Old", normalized_name="old", incorporation_date=date(2010, 1, 1))
        db.add_all([new_co, old_co])
        db.commit()

        results = search_companies_advanced(
            db, cond("incorporation_date", FilterOperator.GT, "2015-01-01", FilterDataType.DATE)
        )
        ids = {r.company.id for r in results}
        assert new_co.id in ids
        assert old_co.id not in ids


class TestNumericFilters:
    def test_exact_employee_count_gte(self, db):
        match = _company(employee_count=50, employee_range_min=None, employee_range_max=None)
        db.add(match)
        db.commit()

        results = search_companies_advanced(
            db, cond("employees", FilterOperator.GTE, 20, FilterDataType.NUMBER)
        )
        assert match.id in {r.company.id for r in results}
        result = next(r for r in results if r.company.id == match.id)
        assert result.match_strength == MatchStrength.DEFINITE

    def test_estimated_range_definite_vs_possible(self, db):
        # entire range clears the bar -> definite
        definite = _company(employee_count=None, employee_range_min=30, employee_range_max=100)
        # range straddles the bar -> possible
        possible = _company(
            canonical_name="Possible Co", normalized_name="possible co",
            employee_count=None, employee_range_min=10, employee_range_max=30,
        )
        # range entirely below the bar -> excluded
        excluded = _company(
            canonical_name="Excluded Co", normalized_name="excluded co",
            employee_count=None, employee_range_min=1, employee_range_max=10,
        )
        db.add_all([definite, possible, excluded])
        db.commit()

        results = {
            r.company.id: r.match_strength
            for r in search_companies_advanced(db, cond("employees", FilterOperator.GTE, 20, FilterDataType.NUMBER))
        }
        assert results[definite.id] == MatchStrength.DEFINITE
        assert results[possible.id] == MatchStrength.POSSIBLE
        assert excluded.id not in results

    def test_between(self, db):
        match = _company(annual_revenue_inr=150_000_000)
        too_high = _company(canonical_name="High", normalized_name="high", annual_revenue_inr=500_000_000)
        db.add_all([match, too_high])
        db.commit()

        results = search_companies_advanced(
            db,
            cond("revenue_inr", FilterOperator.BETWEEN, [100_000_000, 200_000_000], FilterDataType.NUMBER),
        )
        ids = {r.company.id for r in results}
        assert match.id in ids
        assert too_high.id not in ids

    def test_null_revenue_never_treated_as_zero(self, db):
        unknown = _company(
            canonical_name="Unknown Rev", normalized_name="unknown rev",
            annual_revenue_inr=None, revenue_range_min_inr=None, revenue_range_max_inr=None,
        )
        db.add(unknown)
        db.commit()

        # A >= 0 filter would match if NULL were coerced to 0. It must not.
        results = search_companies_advanced(
            db, cond("revenue_inr", FilterOperator.GTE, 0, FilterDataType.NUMBER)
        )
        assert unknown.id not in {r.company.id for r in results}


class TestBooleanComposition:
    def test_and(self, db):
        match = _company(state="Maharashtra", industry="Manufacturing")
        wrong_industry = _company(
            canonical_name="Wrong Industry", normalized_name="wrong industry",
            state="Maharashtra", industry="Retail",
        )
        db.add_all([match, wrong_industry])
        db.commit()

        group = FilterGroup(
            op="AND",
            conditions=[cond("state", FilterOperator.EQ, "Maharashtra"), cond("industry", FilterOperator.EQ, "Manufacturing")],
        )
        ids = {r.company.id for r in search_companies_advanced(db, group)}
        assert match.id in ids
        assert wrong_industry.id not in ids

    def test_or(self, db):
        mh = _company(state="Maharashtra")
        gj = _company(canonical_name="GJ", normalized_name="gj", state="Gujarat")
        ka = _company(canonical_name="KA", normalized_name="ka", state="Karnataka")
        db.add_all([mh, gj, ka])
        db.commit()

        group = FilterGroup(
            op="OR",
            conditions=[cond("state", FilterOperator.EQ, "Maharashtra"), cond("state", FilterOperator.EQ, "Gujarat")],
        )
        ids = {r.company.id for r in search_companies_advanced(db, group)}
        assert mh.id in ids and gj.id in ids
        assert ka.id not in ids

    def test_not(self, db):
        manufacturer = _company(company_category="manufacturer")
        distributor = _company(canonical_name="Dist", normalized_name="dist", company_category="distributor")
        db.add_all([manufacturer, distributor])
        db.commit()

        group = FilterGroup(
            op="NOT",
            conditions=[cond("company_category", FilterOperator.EQ, "manufacturer", FilterDataType.ENUM)],
        )
        ids = {r.company.id for r in search_companies_advanced(db, group)}
        assert distributor.id in ids
        assert manufacturer.id not in ids

    def test_nested_and_or(self, db):
        # (state = Maharashtra AND industry = Manufacturing) OR revenue >= 10cr
        mh_mfg = _company(state="Maharashtra", industry="Manufacturing", annual_revenue_inr=1)
        high_rev_elsewhere = _company(
            canonical_name="High Rev", normalized_name="high rev",
            state="Gujarat", industry="Retail", annual_revenue_inr=200_000_000,
        )
        neither = _company(
            canonical_name="Neither", normalized_name="neither",
            state="Gujarat", industry="Retail", annual_revenue_inr=1,
        )
        db.add_all([mh_mfg, high_rev_elsewhere, neither])
        db.commit()

        group = FilterGroup(
            op="OR",
            conditions=[
                FilterGroup(
                    op="AND",
                    conditions=[
                        cond("state", FilterOperator.EQ, "Maharashtra"),
                        cond("industry", FilterOperator.EQ, "Manufacturing"),
                    ],
                ),
                cond("revenue_inr", FilterOperator.GTE, 100_000_000, FilterDataType.NUMBER),
            ],
        )
        ids = {r.company.id for r in search_companies_advanced(db, group)}
        assert mh_mfg.id in ids
        assert high_rev_elsewhere.id in ids
        assert neither.id not in ids


class TestSort:
    def test_sort_ascending(self, db):
        low = _company(canonical_name="Low", normalized_name="low", employee_count=10, employee_range_min=None, employee_range_max=None)
        high = _company(canonical_name="High", normalized_name="high", employee_count=100, employee_range_min=None, employee_range_max=None)
        db.add_all([low, high])
        db.commit()

        results = search_companies_advanced(
            db,
            cond("employees", FilterOperator.GTE, 0, FilterDataType.NUMBER),
            sort=[SortSpec(field="employees", direction=SortDirection.ASC)],
        )
        names = [r.company.canonical_name for r in results]
        assert names.index("Low") < names.index("High")

    def test_sort_descending_is_default_direction(self, db):
        low = _company(canonical_name="Low2", normalized_name="low2", employee_count=10, employee_range_min=None, employee_range_max=None)
        high = _company(canonical_name="High2", normalized_name="high2", employee_count=100, employee_range_min=None, employee_range_max=None)
        db.add_all([low, high])
        db.commit()

        results = search_companies_advanced(
            db,
            cond("employees", FilterOperator.GTE, 0, FilterDataType.NUMBER),
            sort=[SortSpec(field="employees")],
        )
        names = [r.company.canonical_name for r in results]
        assert names.index("High2") < names.index("Low2")

    def test_unknown_values_sort_last_regardless_of_direction(self, db):
        known = _company(
            canonical_name="Known", normalized_name="known",
            annual_revenue_inr=100, revenue_range_min_inr=None, revenue_range_max_inr=None,
        )
        unknown = _company(
            canonical_name="Unk", normalized_name="unk",
            annual_revenue_inr=None, revenue_range_min_inr=None, revenue_range_max_inr=None,
        )
        db.add_all([known, unknown])
        db.commit()

        # Both companies have the same default state ("Maharashtra") from
        # _company(), so this condition matches both without depending on
        # revenue -- isolating the sort behavior under test. ASC would
        # normally put NULLs first in Postgres; must not here.
        results = search_companies_advanced(
            db,
            cond("state", FilterOperator.EXISTS, data_type=FilterDataType.STRING),
            sort=[SortSpec(field="revenue_inr", direction=SortDirection.ASC)],
        )
        names = [r.company.canonical_name for r in results]
        assert names.index("Known") < names.index("Unk")

    def test_unknown_sort_field_raises(self, db):
        from app.search.filter_registry import UnknownFilterFieldError

        with pytest.raises(UnknownFilterFieldError):
            search_companies_advanced(
                db, cond("state", FilterOperator.EQ, "Maharashtra"), sort=[SortSpec(field="not_a_real_field")]
            )


class TestUnknownHandling:
    def test_definite_only_excludes_possible(self, db):
        definite = _company(employee_count=50, employee_range_min=None, employee_range_max=None)
        possible = _company(
            canonical_name="Possible", normalized_name="possible",
            employee_count=None, employee_range_min=10, employee_range_max=30,
        )
        db.add_all([definite, possible])
        db.commit()

        results = search_companies_advanced(
            db,
            cond("employees", FilterOperator.GTE, 20, FilterDataType.NUMBER),
            unknown_handling=UnknownHandling.DEFINITE_ONLY,
        )
        ids = {r.company.id for r in results}
        assert definite.id in ids
        assert possible.id not in ids

    def test_unknown_bucket_is_separate_from_main_results(self, db):
        matches = _company(state="Maharashtra", annual_revenue_inr=200_000_000)
        unknown_revenue = _company(
            canonical_name="Unknown Rev", normalized_name="unknown rev",
            state="Maharashtra", annual_revenue_inr=None, revenue_range_min_inr=None, revenue_range_max_inr=None,
        )
        fails_outright = _company(
            canonical_name="Fails", normalized_name="fails",
            state="Maharashtra", annual_revenue_inr=1,
        )
        db.add_all([matches, unknown_revenue, fails_outright])
        db.commit()

        group = FilterGroup(
            op="AND",
            conditions=[
                cond("state", FilterOperator.EQ, "Maharashtra"),
                cond("revenue_inr", FilterOperator.GTE, 100_000_000, FilterDataType.NUMBER),
            ],
        )

        main_ids = {r.company.id for r in search_companies_advanced(db, group)}
        assert matches.id in main_ids
        assert unknown_revenue.id not in main_ids
        assert fails_outright.id not in main_ids

        unknown_ids = {c.id for c in find_unknown_bucket(db, group)}
        assert unknown_revenue.id in unknown_ids
        assert matches.id not in unknown_ids
        assert fails_outright.id not in unknown_ids

    def test_unknown_bucket_empty_for_or_trees(self, db):
        group = FilterGroup(
            op="OR",
            conditions=[cond("state", FilterOperator.EQ, "Maharashtra"), cond("state", FilterOperator.EQ, "Gujarat")],
        )
        assert find_unknown_bucket(db, group) == []
