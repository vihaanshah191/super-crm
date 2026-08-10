"""Tests for the frontend-facing read/action endpoints added in this
milestone: company financials/GST registrations, ingestion status, and the
entity-resolution review queue."""

from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.models.company import Company
from app.models.financials import CompanyFinancials
from app.models.gst_registration import CompanyGSTRegistration
from app.models.ingestion_job import IngestionJob
from app.models.match_candidate import EntityMatchCandidate
from app.models.observation import RawObservation
from app.models.source import Source

client = TestClient(app)


def _company(**overrides) -> Company:
    defaults = dict(canonical_name="Test Co", normalized_name="test co", confidence=0.5)
    defaults.update(overrides)
    return Company(**defaults)


class TestCompanyFinancialsEndpoint:
    def test_returns_full_financial_year_history(self, db):
        company = _company()
        db.add(company)
        db.flush()
        db.add_all(
            [
                CompanyFinancials(company_id=company.id, financial_year="FY2024", annual_revenue_inr=100_000_000),
                CompanyFinancials(company_id=company.id, financial_year="FY2025", annual_revenue_inr=150_000_000),
            ]
        )
        db.commit()

        response = client.get(f"/api/companies/{company.id}/financials")
        assert response.status_code == 200
        years = [row["financial_year"] for row in response.json()]
        assert years == ["FY2024", "FY2025"]

    def test_404_for_unknown_company(self, db):
        response = client.get("/api/companies/00000000-0000-0000-0000-000000000000/financials")
        assert response.status_code == 404


class TestCompanyGSTRegistrationsEndpoint:
    def test_returns_all_registrations_primary_first(self, db):
        company = _company()
        db.add(company)
        db.flush()
        db.add_all(
            [
                CompanyGSTRegistration(company_id=company.id, gstin="27ABCDE1234F1Z5", is_primary=False),
                CompanyGSTRegistration(company_id=company.id, gstin="24ABCDE1234F1Z6", is_primary=True),
            ]
        )
        db.commit()

        response = client.get(f"/api/companies/{company.id}/gst-registrations")
        assert response.status_code == 200
        rows = response.json()
        assert len(rows) == 2
        assert rows[0]["is_primary"] is True


class TestIngestionEndpoints:
    def test_list_sources(self, db):
        db.add(
            Source(
                name="test_source_for_api",
                source_type="government_dataset",
                collection_enabled=False,
                reliability_weight=90,
            )
        )
        db.commit()

        response = client.get("/api/ingestion/sources")
        assert response.status_code == 200
        assert any(s["name"] == "test_source_for_api" for s in response.json())

    def test_list_jobs_filters_by_status(self, db):
        source = Source(name="job_test_source", source_type="government_dataset", collection_enabled=False)
        db.add(source)
        db.flush()
        db.add_all(
            [
                IngestionJob(source_id=source.id, status="success", idempotency_key="k1"),
                IngestionJob(source_id=source.id, status="failed", idempotency_key="k2"),
            ]
        )
        db.commit()

        response = client.get("/api/ingestion/jobs", params={"status": "failed"})
        assert response.status_code == 200
        statuses = {j["status"] for j in response.json()}
        assert statuses == {"failed"}

    def test_source_health_derives_from_job_history(self, db):
        source = Source(name="health_test_source", source_type="government_dataset", collection_enabled=True)
        db.add(source)
        db.flush()
        db.add_all(
            [
                IngestionJob(
                    source_id=source.id, status="failed", idempotency_key="k1", error_summary="boom", records_updated=0
                ),
                IngestionJob(source_id=source.id, status="success", idempotency_key="k2", records_updated=4),
            ]
        )
        db.commit()

        response = client.get("/api/ingestion/sources/health")
        assert response.status_code == 200
        entry = next(h for h in response.json() if h["source"]["name"] == "health_test_source")
        assert entry["last_run_status"] == "success"
        assert entry["last_error"] == "boom"
        assert entry["records_collected_total"] == 4
        assert entry["total_jobs"] == 2

    def test_source_health_no_jobs_reports_no_last_run(self, db):
        source = Source(name="no_jobs_source", source_type="government_dataset", collection_enabled=False)
        db.add(source)
        db.commit()

        response = client.get("/api/ingestion/sources/health")
        assert response.status_code == 200
        entry = next(h for h in response.json() if h["source"]["name"] == "no_jobs_source")
        assert entry["last_successful_run"] is None
        assert entry["total_jobs"] == 0


class TestReviewQueueEndpoints:
    def _pending_candidate(self, db):
        source = Source(name="review_test_source", source_type="website", collection_enabled=True, reliability_weight=40)
        db.add(source)
        db.flush()

        candidate_company = _company(canonical_name="Existing Co", normalized_name="existing co")
        db.add(candidate_company)
        db.flush()

        observation = RawObservation(
            company_id=None,
            source_id=source.id,
            source_type=source.source_type,
            field="canonical_name",
            raw_value="Existing Co",
            normalized_value="existing co",
            collected_at=datetime.now(timezone.utc),
            confidence=0.6,
            verification_type="observed",
            collector_version="test/1.0",
        )
        db.add(observation)
        db.flush()

        candidate = EntityMatchCandidate(
            observation_id=observation.id,
            candidate_company_id=candidate_company.id,
            incoming_payload={"canonical_name": "existing co"},
            match_score=0.75,
            matched_signals={"name_similarity": 1.0},
            status="pending",
        )
        db.add(candidate)
        db.commit()
        return candidate, candidate_company

    def test_list_pending_includes_candidate_company_detail(self, db):
        candidate, candidate_company = self._pending_candidate(db)

        response = client.get("/api/review-queue")
        assert response.status_code == 200
        rows = response.json()
        assert any(r["id"] == str(candidate.id) for r in rows)
        matching = next(r for r in rows if r["id"] == str(candidate.id))
        assert matching["candidate_company"]["id"] == str(candidate_company.id)

    def test_confirm_removes_from_pending_queue(self, db):
        candidate, candidate_company = self._pending_candidate(db)

        response = client.post(f"/api/review-queue/{candidate.id}/confirm", json={"reviewed_by": "test-reviewer"})
        assert response.status_code == 200
        assert response.json()["id"] == str(candidate_company.id)

        remaining = client.get("/api/review-queue").json()
        assert not any(r["id"] == str(candidate.id) for r in remaining)

    def test_reject_removes_from_pending_queue(self, db):
        candidate, _ = self._pending_candidate(db)

        response = client.post(f"/api/review-queue/{candidate.id}/reject", json={"reviewed_by": "test-reviewer"})
        assert response.status_code == 204

        remaining = client.get("/api/review-queue").json()
        assert not any(r["id"] == str(candidate.id) for r in remaining)

    def test_confirm_unknown_candidate_returns_400(self, db):
        response = client.post(
            "/api/review-queue/00000000-0000-0000-0000-000000000000/confirm",
            json={"reviewed_by": "test-reviewer"},
        )
        assert response.status_code == 400
