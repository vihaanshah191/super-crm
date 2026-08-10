"""HTTP-level tests for POST /api/search/companies/advanced -- mainly to
prove the FilterGroup/FilterCondition union type round-trips correctly
across a real JSON request body (not just as in-process Python objects, as
tests/test_filter_engine.py exercises)."""

from fastapi.testclient import TestClient

from app.main import app
from app.models.company import Company

client = TestClient(app)


def _company(**overrides) -> Company:
    defaults = dict(canonical_name="Test Co", normalized_name="test co", confidence=0.5, country="India")
    defaults.update(overrides)
    return Company(**defaults)


class TestAdvancedSearchEndpoint:
    def test_single_condition(self, db):
        match = _company(state="Maharashtra")
        other = _company(canonical_name="Other", normalized_name="other", state="Gujarat")
        db.add_all([match, other])
        db.commit()

        response = client.post(
            "/api/search/companies/advanced",
            json={"filter": {"field": "state", "operator": "=", "value": "Maharashtra", "data_type": "string"}},
        )
        assert response.status_code == 200
        body = response.json()
        names = {r["company"]["canonical_name"] for r in body["results"]}
        assert "Test Co" in names
        assert "Other" not in names
        assert body["results"][0]["match_strength"] == "definite"

    def test_nested_and_or_group(self, db):
        match = _company(state="Maharashtra", industry="Manufacturing")
        no_match = _company(canonical_name="No Match", normalized_name="no match", state="Gujarat", industry="Retail")
        db.add_all([match, no_match])
        db.commit()

        response = client.post(
            "/api/search/companies/advanced",
            json={
                "filter": {
                    "op": "AND",
                    "conditions": [
                        {"field": "state", "operator": "=", "value": "Maharashtra", "data_type": "string"},
                        {"field": "industry", "operator": "=", "value": "Manufacturing", "data_type": "string"},
                    ],
                }
            },
        )
        assert response.status_code == 200
        names = {r["company"]["canonical_name"] for r in response.json()["results"]}
        assert "Test Co" in names
        assert "No Match" not in names

    def test_invalid_field_returns_422(self, db):
        response = client.post(
            "/api/search/companies/advanced",
            json={"filter": {"field": "not_a_real_field", "operator": "=", "value": "x", "data_type": "string"}},
        )
        assert response.status_code == 422

    def test_invalid_operator_for_type_rejected_before_execution(self, db):
        response = client.post(
            "/api/search/companies/advanced",
            json={"filter": {"field": "incorporation_date", "operator": "CONTAINS", "value": "2020", "data_type": "date"}},
        )
        assert response.status_code == 422

    def test_unknown_handling_include_unknown_separately(self, db):
        matches = _company(state="Maharashtra", annual_revenue_inr=200_000_000)
        unknown_revenue = _company(
            canonical_name="Unknown Rev", normalized_name="unknown rev",
            state="Maharashtra", annual_revenue_inr=None,
        )
        db.add_all([matches, unknown_revenue])
        db.commit()

        response = client.post(
            "/api/search/companies/advanced",
            json={
                "filter": {
                    "op": "AND",
                    "conditions": [
                        {"field": "state", "operator": "=", "value": "Maharashtra", "data_type": "string"},
                        {"field": "revenue_inr", "operator": ">=", "value": 100_000_000, "data_type": "number"},
                    ],
                },
                "unknown_handling": "include_unknown_separately",
            },
        )
        assert response.status_code == 200
        body = response.json()
        main_names = {r["company"]["canonical_name"] for r in body["results"]}
        unknown_names = {c["canonical_name"] for c in body["unknown_results"]}
        assert "Test Co" in main_names
        assert "Unknown Rev" in unknown_names
        assert "Unknown Rev" not in main_names
