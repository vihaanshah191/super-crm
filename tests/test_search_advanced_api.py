"""HTTP-level tests for POST /api/search/companies/advanced -- mainly to
prove the FilterGroup/FilterCondition union type round-trips correctly
across a real JSON request body (not just as in-process Python objects, as
tests/test_filter_engine.py exercises)."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.models.company import Company
from app.models.observation import RawObservation

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

    def test_data_type_mismatching_the_registry_returns_422(self, db):
        """'confidence' is registered as NUMBER; a client claiming STRING
        passes Pydantic's own per-data_type operator check (CONTAINS is
        valid for STRING in general) but must still be rejected once
        cross-checked against what the registry actually says confidence
        is -- not crash inside the string compiler on a numeric column."""
        response = client.post(
            "/api/search/companies/advanced",
            json={"filter": {"field": "confidence", "operator": "CONTAINS", "value": "x", "data_type": "string"}},
        )
        assert response.status_code == 422

    def test_operator_outside_fields_own_allowed_set_returns_422(self, db):
        """EQ is valid for DATE in general, but last_verified_at's own
        FieldSpec.allowed_operators restricts it to ordering/EXISTS
        operators -- a stricter, per-field rule Pydantic's data_type-level
        check alone can't enforce."""
        response = client.post(
            "/api/search/companies/advanced",
            json={"filter": {"field": "last_verified_at", "operator": "=", "value": "2024-01-01", "data_type": "date"}},
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

    def test_sources_field_lists_real_source_names_not_just_a_count(self, db, website_source, mca_source):
        company = _company(state="Maharashtra")
        db.add(company)
        db.commit()
        db.add_all(
            [
                RawObservation(
                    company_id=company.id, source_id=website_source.id, source_type="website",
                    field="canonical_name", raw_value="Test Co", normalized_value="test co",
                    collected_at=datetime.now(timezone.utc), confidence=0.5, verification_type="observed",
                    collector_version="test/1.0",
                ),
                RawObservation(
                    company_id=company.id, source_id=mca_source.id, source_type="government_dataset",
                    field="canonical_name", raw_value="Test Co", normalized_value="test co",
                    collected_at=datetime.now(timezone.utc), confidence=0.95, verification_type="verified",
                    collector_version="test/1.0",
                ),
            ]
        )
        db.commit()

        response = client.post(
            "/api/search/companies/advanced",
            json={"filter": {"field": "state", "operator": "=", "value": "Maharashtra", "data_type": "string"}},
        )
        assert response.status_code == 200
        result = response.json()["results"][0]
        assert set(result["sources"]) == {website_source.name, mca_source.name}

    def test_country_scope_restricts_results(self, db):
        india = _company(state="Maharashtra", country_code="IN")
        us = _company(canonical_name="US Co", normalized_name="us co", state="Maharashtra", country_code="US")
        db.add_all([india, us])
        db.commit()

        response = client.post(
            "/api/search/companies/advanced",
            json={
                "filter": {"field": "state", "operator": "=", "value": "Maharashtra", "data_type": "string"},
                "country_scope": ["IN"],
            },
        )
        assert response.status_code == 200
        names = {r["company"]["canonical_name"] for r in response.json()["results"]}
        assert "Test Co" in names
        assert "US Co" not in names

    def test_source_scope_restricts_results(self, db, website_source, mca_source):
        from_website = _company(state="Maharashtra")
        from_mca = _company(canonical_name="MCA Co", normalized_name="mca co", state="Maharashtra")
        db.add_all([from_website, from_mca])
        db.commit()
        db.add_all(
            [
                RawObservation(
                    company_id=from_website.id, source_id=website_source.id, source_type="website",
                    field="canonical_name", raw_value="x", normalized_value="x",
                    collected_at=datetime.now(timezone.utc), confidence=0.5, verification_type="observed",
                    collector_version="test/1.0",
                ),
                RawObservation(
                    company_id=from_mca.id, source_id=mca_source.id, source_type="government_dataset",
                    field="canonical_name", raw_value="x", normalized_value="x",
                    collected_at=datetime.now(timezone.utc), confidence=0.95, verification_type="verified",
                    collector_version="test/1.0",
                ),
            ]
        )
        db.commit()

        response = client.post(
            "/api/search/companies/advanced",
            json={
                "filter": {"field": "state", "operator": "=", "value": "Maharashtra", "data_type": "string"},
                "source_scope": [str(website_source.id)],
            },
        )
        assert response.status_code == 200
        names = {r["company"]["canonical_name"] for r in response.json()["results"]}
        assert "Test Co" in names
        assert "MCA Co" not in names
