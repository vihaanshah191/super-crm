"""Tests for saved searches (Phase 6, docs/multi_source_architecture.md):
CRUD, execute (reusing the Phase 3/4 filter engine), country/source scope,
sort, and selected_fields validation."""

from fastapi.testclient import TestClient

from app.main import app
from app.models.company import Company
from app.models.observation import RawObservation
from app.models.source import Source

client = TestClient(app)


def _company(**overrides) -> Company:
    defaults = dict(canonical_name="Test Co", normalized_name="test co", confidence=0.5, country="India")
    defaults.update(overrides)
    return Company(**defaults)


SIMPLE_FILTER = {"field": "state", "operator": "=", "value": "Maharashtra", "data_type": "string"}


class TestCreate:
    def test_create_minimal(self, db):
        response = client.post(
            "/api/saved-searches",
            json={"name": "My Maharashtra manufacturers", "created_by": "alice", "filter_definition": SIMPLE_FILTER},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "My Maharashtra manufacturers"
        assert body["created_by"] == "alice"
        assert body["filter_definition"] == SIMPLE_FILTER
        assert body["country_scope"] == []
        assert body["source_scope"] == []
        assert body["sort"] == []
        assert body["selected_fields"] == []

    def test_create_with_full_fields(self, db):
        source = Source(name="scope_test_source", source_type="government_dataset", collection_enabled=True)
        db.add(source)
        db.commit()

        response = client.post(
            "/api/saved-searches",
            json={
                "name": "Indian exporters",
                "created_by": "bob",
                "country_scope": ["IN"],
                "source_scope": [str(source.id)],
                "filter_definition": {
                    "op": "AND",
                    "conditions": [SIMPLE_FILTER, {"field": "export_status", "operator": "=", "value": True, "data_type": "boolean"}],
                },
                "sort": [{"field": "confidence", "direction": "desc"}],
                "selected_fields": ["canonical_name", "state", "confidence"],
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["country_scope"] == ["IN"]
        assert body["source_scope"] == [str(source.id)]
        assert body["sort"] == [{"field": "confidence", "direction": "desc"}]
        assert body["selected_fields"] == ["canonical_name", "state", "confidence"]

    def test_unknown_selected_field_rejected(self, db):
        response = client.post(
            "/api/saved-searches",
            json={
                "name": "Bad",
                "created_by": "alice",
                "filter_definition": SIMPLE_FILTER,
                "selected_fields": ["not_a_real_field"],
            },
        )
        assert response.status_code == 422

    def test_unknown_filter_field_rejected_at_the_filter_type_layer(self, db):
        response = client.post(
            "/api/saved-searches",
            json={
                "name": "Bad filter",
                "created_by": "alice",
                "filter_definition": {"field": "state", "operator": "CONTAINS", "value": "x", "data_type": "date"},
            },
        )
        assert response.status_code == 422


class TestListGetDelete:
    def test_list_filters_by_created_by(self, db):
        client.post("/api/saved-searches", json={"name": "A", "created_by": "alice", "filter_definition": SIMPLE_FILTER})
        client.post("/api/saved-searches", json={"name": "B", "created_by": "bob", "filter_definition": SIMPLE_FILTER})

        response = client.get("/api/saved-searches", params={"created_by": "alice"})
        assert response.status_code == 200
        names = {s["name"] for s in response.json()}
        assert "A" in names
        assert "B" not in names

    def test_get_by_id(self, db):
        created = client.post(
            "/api/saved-searches", json={"name": "Findable", "created_by": "alice", "filter_definition": SIMPLE_FILTER}
        ).json()
        response = client.get(f"/api/saved-searches/{created['id']}")
        assert response.status_code == 200
        assert response.json()["name"] == "Findable"

    def test_get_missing_is_404(self, db):
        response = client.get("/api/saved-searches/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404

    def test_delete(self, db):
        created = client.post(
            "/api/saved-searches", json={"name": "Deletable", "created_by": "alice", "filter_definition": SIMPLE_FILTER}
        ).json()
        response = client.delete(f"/api/saved-searches/{created['id']}")
        assert response.status_code == 204
        assert client.get(f"/api/saved-searches/{created['id']}").status_code == 404


class TestUpdate:
    def test_partial_update_only_changes_given_fields(self, db):
        created = client.post(
            "/api/saved-searches",
            json={"name": "Original", "created_by": "alice", "filter_definition": SIMPLE_FILTER},
        ).json()

        response = client.patch(f"/api/saved-searches/{created['id']}", json={"name": "Renamed"})
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Renamed"
        assert body["created_by"] == "alice"  # untouched
        assert body["filter_definition"] == SIMPLE_FILTER  # untouched

    def test_update_filter_definition(self, db):
        created = client.post(
            "/api/saved-searches", json={"name": "X", "created_by": "alice", "filter_definition": SIMPLE_FILTER}
        ).json()
        new_filter = {"field": "state", "operator": "=", "value": "Gujarat", "data_type": "string"}

        response = client.patch(f"/api/saved-searches/{created['id']}", json={"filter_definition": new_filter})
        assert response.status_code == 200
        assert response.json()["filter_definition"] == new_filter


class TestExecute:
    def test_execute_runs_the_stored_filter(self, db):
        match = _company(state="Maharashtra")
        other = _company(canonical_name="Other", normalized_name="other", state="Gujarat")
        db.add_all([match, other])
        db.commit()

        created = client.post(
            "/api/saved-searches", json={"name": "MH", "created_by": "alice", "filter_definition": SIMPLE_FILTER}
        ).json()
        response = client.post(f"/api/saved-searches/{created['id']}/execute", json={})
        assert response.status_code == 200
        names = {r["company"]["canonical_name"] for r in response.json()["results"]}
        assert "Test Co" in names
        assert "Other" not in names

    def test_execute_missing_saved_search_is_404(self, db):
        response = client.post(
            "/api/saved-searches/00000000-0000-0000-0000-000000000000/execute", json={}
        )
        assert response.status_code == 404

    def test_execute_rejects_filter_mismatching_the_registry(self, db):
        """'confidence' is registered as NUMBER; claiming STRING with
        CONTAINS passes Pydantic's own data_type-operator check (valid for
        STRING in general) at creation time, so the mismatch is only
        catchable once cross-checked against the registry -- which happens
        at execute time, not creation time."""
        mismatched_filter = {"field": "confidence", "operator": "CONTAINS", "value": "x", "data_type": "string"}
        created = client.post(
            "/api/saved-searches",
            json={"name": "Bad registry match", "created_by": "alice", "filter_definition": mismatched_filter},
        ).json()

        response = client.post(f"/api/saved-searches/{created['id']}/execute", json={})
        assert response.status_code == 422

    def test_execute_applies_country_scope(self, db):
        india = _company(state="Maharashtra", country_code="IN")
        us = _company(canonical_name="US Co", normalized_name="us co", state="Maharashtra", country_code="US")
        db.add_all([india, us])
        db.commit()

        created = client.post(
            "/api/saved-searches",
            json={
                "name": "India only",
                "created_by": "alice",
                "country_scope": ["IN"],
                "filter_definition": SIMPLE_FILTER,
            },
        ).json()
        response = client.post(f"/api/saved-searches/{created['id']}/execute", json={})
        names = {r["company"]["canonical_name"] for r in response.json()["results"]}
        assert "Test Co" in names
        assert "US Co" not in names

    def test_execute_applies_source_scope(self, db):
        source_a = Source(name="scope_src_a", source_type="government_dataset", collection_enabled=True)
        source_b = Source(name="scope_src_b", source_type="government_dataset", collection_enabled=True)
        db.add_all([source_a, source_b])
        db.flush()

        from_a = _company(canonical_name="From A", normalized_name="from a", state="Maharashtra")
        from_b = _company(canonical_name="From B", normalized_name="from b", state="Maharashtra")
        db.add_all([from_a, from_b])
        db.flush()

        db.add_all(
            [
                RawObservation(
                    company_id=from_a.id, source_id=source_a.id, source_type="government_dataset",
                    field="state", raw_value="Maharashtra", normalized_value="maharashtra",
                    collected_at=from_a.created_at, verification_type="verified", collector_version="test/1.0",
                ),
                RawObservation(
                    company_id=from_b.id, source_id=source_b.id, source_type="government_dataset",
                    field="state", raw_value="Maharashtra", normalized_value="maharashtra",
                    collected_at=from_b.created_at, verification_type="verified", collector_version="test/1.0",
                ),
            ]
        )
        db.commit()

        created = client.post(
            "/api/saved-searches",
            json={
                "name": "Source A only",
                "created_by": "alice",
                "source_scope": [str(source_a.id)],
                "filter_definition": SIMPLE_FILTER,
            },
        ).json()
        response = client.post(f"/api/saved-searches/{created['id']}/execute", json={})
        names = {r["company"]["canonical_name"] for r in response.json()["results"]}
        assert "From A" in names
        assert "From B" not in names

    def test_execute_applies_stored_sort(self, db):
        low = _company(
            canonical_name="Low", normalized_name="low", state="Maharashtra",
            employee_count=10, employee_range_min=None, employee_range_max=None,
        )
        high = _company(
            canonical_name="High", normalized_name="high", state="Maharashtra",
            employee_count=100, employee_range_min=None, employee_range_max=None,
        )
        db.add_all([low, high])
        db.commit()

        created = client.post(
            "/api/saved-searches",
            json={
                "name": "Sorted",
                "created_by": "alice",
                "filter_definition": SIMPLE_FILTER,
                "sort": [{"field": "employees", "direction": "asc"}],
            },
        ).json()
        response = client.post(f"/api/saved-searches/{created['id']}/execute", json={})
        names = [r["company"]["canonical_name"] for r in response.json()["results"]]
        assert names.index("Low") < names.index("High")

    def test_execute_include_unknown_separately(self, db):
        known = _company(state="Maharashtra", annual_revenue_inr=200_000_000)
        unknown = _company(
            canonical_name="Unknown Rev", normalized_name="unknown rev",
            state="Maharashtra", annual_revenue_inr=None,
        )
        db.add_all([known, unknown])
        db.commit()

        created = client.post(
            "/api/saved-searches",
            json={
                "name": "Revenue filter",
                "created_by": "alice",
                "filter_definition": {
                    "op": "AND",
                    "conditions": [
                        SIMPLE_FILTER,
                        {"field": "revenue_inr", "operator": ">=", "value": 100_000_000, "data_type": "number"},
                    ],
                },
            },
        ).json()
        response = client.post(
            f"/api/saved-searches/{created['id']}/execute", json={"unknown_handling": "include_unknown_separately"}
        )
        body = response.json()
        main_names = {r["company"]["canonical_name"] for r in body["results"]}
        unknown_names = {c["canonical_name"] for c in body["unknown_results"]}
        assert "Test Co" in main_names
        assert "Unknown Rev" in unknown_names
