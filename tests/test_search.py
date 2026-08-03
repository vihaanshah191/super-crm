from app.models.company import Company
from app.search.filters import CompanySearchFilters
from app.search.query import build_company_query


def _company(**overrides) -> Company:
    defaults = dict(
        canonical_name="ABC Industries",
        normalized_name="abc industries",
        state="Maharashtra",
        city="Pune",
        industry="Chemical Manufacturing",
        company_category="manufacturer",
        export_status=True,
        employee_range_min=50,
        employee_range_max=200,
        annual_revenue_inr=150_000_000,
        confidence=0.9,
    )
    defaults.update(overrides)
    return Company(**defaults)


class TestSearchFilters:
    def test_filters_by_state_and_employee_range(self, db):
        match = _company()
        too_small = _company(canonical_name="Tiny Co", normalized_name="tiny co", employee_range_min=2, employee_range_max=5)
        wrong_state = _company(canonical_name="Gujarat Co", normalized_name="gujarat co", state="Gujarat")
        db.add_all([match, too_small, wrong_state])
        db.commit()

        filters = CompanySearchFilters(state="Maharashtra", employee_min=20)
        results = list(db.scalars(build_company_query(filters)))

        assert match.id in {c.id for c in results}
        assert too_small.id not in {c.id for c in results}
        assert wrong_state.id not in {c.id for c in results}

    def test_filters_by_revenue_range(self, db):
        in_range = _company(annual_revenue_inr=150_000_000)  # 15cr
        too_low = _company(canonical_name="Low Rev", normalized_name="low rev", annual_revenue_inr=20_000_000)
        db.add_all([in_range, too_low])
        db.commit()

        filters = CompanySearchFilters(revenue_min_inr=100_000_000)  # 10cr+
        results = {c.id for c in db.scalars(build_company_query(filters))}

        assert in_range.id in results
        assert too_low.id not in results

    def test_filters_by_manufacturer_and_export_status(self, db):
        manufacturer_exporter = _company()
        non_exporter = _company(canonical_name="Local Only", normalized_name="local only", export_status=False)
        db.add_all([manufacturer_exporter, non_exporter])
        db.commit()

        filters = CompanySearchFilters(company_category="manufacturer", export_status=True)
        results = {c.id for c in db.scalars(build_company_query(filters))}

        assert manufacturer_exporter.id in results
        assert non_exporter.id not in results

    def test_confidence_threshold_filter(self, db):
        high_conf = _company(confidence=0.9)
        low_conf = _company(canonical_name="Uncertain Co", normalized_name="uncertain co", confidence=0.2)
        db.add_all([high_conf, low_conf])
        db.commit()

        filters = CompanySearchFilters(min_confidence=0.5)
        results = {c.id for c in db.scalars(build_company_query(filters))}

        assert high_conf.id in results
        assert low_conf.id not in results

    def test_matches_example_query_chemical_manufacturers_in_maharashtra(self, db):
        """"Find chemical manufacturers in Maharashtra with more than 20
        employees and annual revenue above ₹10 crore." -- the example query
        from the product spec, expressed as structured filters."""
        target = _company()
        wrong_industry = _company(
            canonical_name="Steel Works", normalized_name="steel works", industry="Steel Manufacturing"
        )
        db.add_all([target, wrong_industry])
        db.commit()

        filters = CompanySearchFilters(
            industry="Chemical",
            state="Maharashtra",
            employee_min=20,
            revenue_min_inr=100_000_000,
        )
        results = {c.id for c in db.scalars(build_company_query(filters))}

        assert target.id in results
        assert wrong_industry.id not in results

    def test_pagination_limit(self, db):
        for i in range(5):
            db.add(_company(canonical_name=f"Co {i}", normalized_name=f"co {i}"))
        db.commit()

        filters = CompanySearchFilters(limit=2)
        results = list(db.scalars(build_company_query(filters)))
        assert len(results) == 2
