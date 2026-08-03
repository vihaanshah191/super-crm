from datetime import datetime, timezone
from pathlib import Path

from app.compliance.source_policy import SourcePolicy
from app.ingestion.pipeline import confirm_match, ingest_parsed_record, recompute_company_evidence
from app.models.company import Company
from app.models.evidence import Evidence
from app.models.match_candidate import EntityMatchCandidate
from app.models.observation import RawObservation
from app.source_adapters.base import FetchResult
from app.source_adapters.government_dataset_adapter import GovernmentDatasetAdapter
from app.source_adapters.website_adapter import WebsiteAdapter

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _policy(source) -> SourcePolicy:
    return SourcePolicy(
        source_name=source.name,
        collection_enabled=source.collection_enabled,
        rate_limit_per_minute=source.rate_limit_per_minute,
        max_concurrency=source.max_concurrency,
    )


def _mca_record():
    adapter = GovernmentDatasetAdapter(source_name="mca_company_master_data")
    content = (FIXTURES_DIR / "data" / "mca_company_master_maharashtra.csv").read_bytes()
    fr = FetchResult(
        url="https://data.gov.in/mca_fixture.csv",
        status_code=200,
        content=content,
        content_type="text/csv",
        fetched_at=datetime.now(timezone.utc),
    )
    return adapter, adapter.parse(fr)[0]


def _website_record():
    adapter = WebsiteAdapter(source_name="example_company_website")
    content = (FIXTURES_DIR / "html" / "example_company_website.html").read_bytes()
    fr = FetchResult(
        url="https://www.abcindustries.example/about",
        status_code=200,
        content=content,
        content_type="text/html",
        fetched_at=datetime.now(timezone.utc),
    )
    return adapter, adapter.parse(fr)[0]


class TestMultiSourceResolution:
    """The vertical-slice requirement: information from two different
    sources resolves into the same company while retaining separate
    evidence and provenance."""

    def test_government_record_creates_new_company(self, db, mca_source):
        adapter, record = _mca_record()
        result = ingest_parsed_record(db, adapter, mca_source, _policy(mca_source), record)
        db.commit()

        assert result.decision == "new_company"
        company = db.get(Company, result.company_id)
        assert company.cin == "U24100MH2015PTC123456"
        assert company.normalized_name == "abc industries"

    def test_website_record_for_same_company_is_flagged_for_review_not_auto_merged(
        self, db, mca_source, website_source
    ):
        mca_adapter, mca_record = _mca_record()
        ingest_parsed_record(db, mca_adapter, mca_source, _policy(mca_source), mca_record)
        db.commit()

        web_adapter, web_record = _website_record()
        result = ingest_parsed_record(db, web_adapter, website_source, _policy(website_source), web_record)
        db.commit()

        assert result.decision == "review"
        assert result.company_id is None
        pending = db.query(EntityMatchCandidate).filter_by(status="pending").all()
        assert len(pending) == 1

    def test_confirming_review_match_merges_evidence_from_both_sources(self, db, mca_source, website_source):
        mca_adapter, mca_record = _mca_record()
        mca_result = ingest_parsed_record(db, mca_adapter, mca_source, _policy(mca_source), mca_record)
        db.commit()

        web_adapter, web_record = _website_record()
        ingest_parsed_record(db, web_adapter, website_source, _policy(website_source), web_record)
        db.commit()

        pending = db.query(EntityMatchCandidate).filter_by(status="pending").first()
        company = confirm_match(db, pending.id, reviewed_by="test-reviewer")
        db.commit()

        assert company.id == mca_result.company_id
        assert company.source_count == 2

        evidence_fields = {e.field for e in db.query(Evidence).filter_by(company_id=company.id).all()}
        # From MCA (VERIFIED):
        assert "cin" in evidence_fields
        assert "incorporation_date" in evidence_fields
        # From the website (OBSERVED):
        assert "industry" in evidence_fields
        assert "public_email" in evidence_fields
        assert "products" in evidence_fields

        cin_evidence = db.query(Evidence).filter_by(company_id=company.id, field="cin").one()
        assert cin_evidence.verification_type == "verified"
        industry_evidence = db.query(Evidence).filter_by(company_id=company.id, field="industry").one()
        assert industry_evidence.verification_type == "observed"

        # Every observation from both sources is still individually queryable.
        observations = db.query(RawObservation).filter_by(company_id=company.id).all()
        source_ids = {o.source_id for o in observations}
        assert source_ids == {mca_source.id, website_source.id}


class TestDuplicatePreventionAndConflicts:
    def test_reingesting_identical_mca_record_does_not_duplicate_company(self, db, mca_source):
        adapter, record = _mca_record()
        result1 = ingest_parsed_record(db, adapter, mca_source, _policy(mca_source), record)
        db.commit()
        result2 = ingest_parsed_record(db, adapter, mca_source, _policy(mca_source), record)
        db.commit()

        assert result1.company_id == result2.company_id
        assert db.query(Company).count() == 1

    def test_conflicting_state_observations_are_both_preserved_with_provenance(self, db, mca_source):
        adapter, record = _mca_record()
        result = ingest_parsed_record(db, adapter, mca_source, _policy(mca_source), record)
        db.commit()
        company_id = result.company_id

        # A second (hypothetically incorrect) source reports a different state.
        from app.models.observation import RawObservation
        from app.models.source import Source

        other_source = Source(
            name="other_directory",
            source_type="directory",
            collection_enabled=True,
            reliability_weight=30,
        )
        db.add(other_source)
        db.commit()
        db.add(
            RawObservation(
                company_id=company_id,
                source_id=other_source.id,
                source_type="directory",
                field="state",
                raw_value="Gujarat",
                normalized_value="gujarat",
                collected_at=datetime.now(timezone.utc),
                confidence=0.3,
                verification_type="observed",
                collector_version="test/1.0",
            )
        )
        db.commit()

        recompute_company_evidence(db, company_id)
        db.commit()

        state_observations = db.query(RawObservation).filter_by(company_id=company_id, field="state").all()
        assert {o.normalized_value for o in state_observations} == {"maharashtra", "gujarat"}

        state_evidence = db.query(Evidence).filter_by(company_id=company_id, field="state").one()
        # MCA (verified, majority) should win over the single conflicting directory observation.
        assert state_evidence.value == "maharashtra"
        assert state_evidence.verification_type == "verified"
        # Confidence reflects the disagreement -- HIGH, not perfect.
        assert 0.5 < state_evidence.confidence < 0.95

    def test_idempotent_reprocessing_is_safe(self, db, mca_source):
        adapter, record = _mca_record()
        result = ingest_parsed_record(db, adapter, mca_source, _policy(mca_source), record)
        db.commit()

        recompute_company_evidence(db, result.company_id)
        recompute_company_evidence(db, result.company_id)
        db.commit()

        evidence_rows = db.query(Evidence).filter_by(company_id=result.company_id).all()
        fields = [e.field for e in evidence_rows]
        assert len(fields) == len(set(fields))  # no duplicate Evidence rows per field
