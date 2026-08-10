import uuid
from datetime import datetime, timezone

from app.ingestion.entity_resolution.matcher import (
    AUTO_MATCH_THRESHOLD,
    IdentitySignals,
    REVIEW_THRESHOLD,
    score_match,
)
from app.ingestion.entity_resolution.resolver import find_candidates, resolve, signals_from_company
from app.models.company import Company


class TestMatcherScoring:
    def test_cin_match_auto_matches(self):
        incoming = IdentitySignals(cin="U24100MH2015PTC123456")
        candidate = IdentitySignals(cin="U24100MH2015PTC123456", normalized_name="abc industries")
        result = score_match(incoming, candidate)
        assert result.decision == "auto_match"
        assert result.score >= AUTO_MATCH_THRESHOLD

    def test_cin_mismatch_does_not_match_on_cin_alone(self):
        incoming = IdentitySignals(cin="U24100MH2015PTC123456")
        candidate = IdentitySignals(cin="U99999MH1999PTC999999")
        result = score_match(incoming, candidate)
        assert result.matched_signals.get("cin_match") is False

    def test_gstin_match_auto_matches(self):
        incoming = IdentitySignals(gstin="27ABCDE1234F1Z5")
        candidate = IdentitySignals(gstin="27ABCDE1234F1Z5")
        result = score_match(incoming, candidate)
        assert result.decision == "auto_match"

    def test_identical_name_alone_never_auto_matches(self):
        """Never automatically merge two companies based solely on similar names."""
        incoming = IdentitySignals(normalized_name="abc industries")
        candidate = IdentitySignals(normalized_name="abc industries")
        result = score_match(incoming, candidate)
        assert result.decision != "auto_match"
        assert result.score < AUTO_MATCH_THRESHOLD

    def test_identical_name_plus_postal_code_still_requires_review(self):
        """Even with a corroborating (but weak) locality signal, name-based
        matches stay in the review band -- only deterministic identifiers
        (CIN/GSTIN) may auto-match."""
        incoming = IdentitySignals(normalized_name="abc industries", postal_code="411019")
        candidate = IdentitySignals(normalized_name="abc industries", postal_code="411019", state="maharashtra")
        result = score_match(incoming, candidate)
        assert result.decision == "review"
        assert result.score < AUTO_MATCH_THRESHOLD

    def test_dissimilar_names_no_match(self):
        incoming = IdentitySignals(normalized_name="abc industries")
        candidate = IdentitySignals(normalized_name="zenith global logistics")
        result = score_match(incoming, candidate)
        assert result.decision == "no_match"
        assert result.score < REVIEW_THRESHOLD

    def test_website_domain_match_alone_is_review_not_auto(self):
        incoming = IdentitySignals(website_domain="abcindustries.example")
        candidate = IdentitySignals(website_domain="abcindustries.example")
        result = score_match(incoming, candidate)
        assert result.decision == "review"


class TestResolverCandidateGeneration:
    def test_find_candidates_by_exact_cin(self, db):
        company = Company(
            canonical_name="ABC Industries",
            normalized_name="abc industries",
            cin="U24100MH2015PTC123456",
            confidence=0.9,
        )
        db.add(company)
        db.commit()

        candidates = find_candidates(db, IdentitySignals(cin="U24100MH2015PTC123456"))
        assert [c.id for c in candidates] == [company.id]

    def test_find_candidates_by_trigram_name_similarity(self, db):
        company = Company(canonical_name="ABC Industries", normalized_name="abc industries", confidence=0.5)
        db.add(company)
        db.commit()

        candidates = find_candidates(db, IdentitySignals(normalized_name="abc industries pvt"))
        assert company.id in {c.id for c in candidates}

    def test_unrelated_company_not_returned_as_candidate(self, db):
        db.add(Company(canonical_name="Zenith Logistics", normalized_name="zenith logistics", confidence=0.5))
        db.commit()

        candidates = find_candidates(db, IdentitySignals(normalized_name="abc industries"))
        assert candidates == []


class TestResolveEndToEnd:
    def test_resolve_auto_matches_on_cin(self, db):
        company = Company(
            canonical_name="ABC Industries",
            normalized_name="abc industries",
            cin="U24100MH2015PTC123456",
            confidence=0.9,
        )
        db.add(company)
        db.commit()

        outcome = resolve(
            db,
            observation_id=company.id,  # any UUID works here, no FK enforced at this call site
            signals=IdentitySignals(cin="U24100MH2015PTC123456"),
            incoming_payload={"cin": "U24100MH2015PTC123456"},
        )
        assert outcome.decision == "auto_match"
        assert outcome.company.id == company.id

    def test_resolve_creates_review_candidate_for_ambiguous_name_match(self, db):
        from app.models.match_candidate import EntityMatchCandidate
        from app.models.observation import RawObservation
        from app.models.source import Source

        source = Source(name="test_source", source_type="website", collection_enabled=True)
        db.add(source)
        db.commit()
        obs = RawObservation(
            source_id=source.id,
            source_type="website",
            field="canonical_name",
            raw_value="ABC Industries",
            normalized_value="abc industries",
            collected_at=datetime.now(timezone.utc),
            confidence=0.5,
            verification_type="observed",
            collector_version="test/1.0",
        )
        db.add(obs)
        db.commit()

        company = Company(
            canonical_name="ABC Industries",
            normalized_name="abc industries",
            postal_code="411019",
            confidence=0.9,
        )
        db.add(company)
        db.commit()

        outcome = resolve(
            db,
            observation_id=obs.id,
            signals=IdentitySignals(normalized_name="abc industries", postal_code="411019"),
            incoming_payload={"canonical_name": "abc industries"},
        )
        assert outcome.decision == "review"
        assert outcome.company is None

        pending = db.query(EntityMatchCandidate).filter_by(observation_id=obs.id).all()
        assert len(pending) == 1
        assert pending[0].candidate_company_id == company.id
        assert pending[0].status == "pending"

    def test_resolve_no_match_for_unrelated_company(self, db):
        db.add(Company(canonical_name="Zenith Logistics", normalized_name="zenith logistics", confidence=0.5))
        db.commit()

        outcome = resolve(
            db,
            observation_id=uuid.uuid4(),
            signals=IdentitySignals(normalized_name="abc industries"),
            incoming_payload={},
        )
        assert outcome.decision == "no_match"

    def test_signals_from_company_extracts_email_domain(self, db):
        company = Company(
            canonical_name="ABC Industries",
            normalized_name="abc industries",
            public_email="info@abcindustries.example",
            confidence=0.5,
        )
        signals = signals_from_company(company)
        assert signals.public_email_domain == "abcindustries.example"
