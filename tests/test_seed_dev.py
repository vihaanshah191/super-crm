from sqlalchemy import select

from app.cli.seed_dev import _SYNTHETIC_COMPANIES, run
from app.ingestion.pipeline import confirm_match
from app.models.company import Company
from app.models.match_candidate import EntityMatchCandidate


class TestSeedDev:
    def test_creates_expected_number_of_synthetic_companies(self, db):
        run()
        companies = list(db.scalars(select(Company)))
        assert len(companies) == len(_SYNTHETIC_COMPANIES)

    def test_synthetic_names_are_obviously_fake(self, db):
        run()
        names = {c.canonical_name for c in db.scalars(select(Company))}
        # Every seeded name uses one of these placeholder-style prefixes --
        # this is the guard against ever seeding a real company identity.
        fake_markers = ("Acme", "Example", "Demo", "Sample", "Placeholder", "Fictitious")
        assert all(any(name.startswith(marker) for marker in fake_markers) for name in names)

    def test_rerunning_is_idempotent(self, db):
        run()
        first_count = len(list(db.scalars(select(Company))))
        run()
        second_count = len(list(db.scalars(select(Company))))
        assert first_count == second_count == len(_SYNTHETIC_COMPANIES)

    def test_seeds_a_pending_review_queue_example(self, db):
        run()
        pending = list(db.scalars(select(EntityMatchCandidate).where(EntityMatchCandidate.status == "pending")))
        assert len(pending) >= 1

    def test_seeded_companies_have_computed_evidence_and_confidence(self, db):
        run()
        companies = list(db.scalars(select(Company)))
        assert all(c.confidence > 0 for c in companies)

    def test_rerun_after_confirming_a_match_does_not_break_idempotency(self, db):
        """Regression test: confirming the seeded review-queue match runs
        recompute_company_evidence(), which can change Company.normalized_name
        to whichever observation's value won. If cleanup on the next seed_dev
        run only matched on normalized_name, it would miss this now-renamed
        company, leave its CIN behind, and the next run's INSERT would hit a
        UNIQUE constraint violation on cin. See _clear_previous_seed()."""
        run()
        candidate = db.scalar(select(EntityMatchCandidate).where(EntityMatchCandidate.status == "pending"))
        confirm_match(db, candidate.id, "test-reviewer")
        db.commit()

        # Re-running must not raise IntegrityError, and must still converge
        # on exactly the expected set of synthetic companies.
        run()
        companies = list(db.scalars(select(Company)))
        assert len(companies) == len(_SYNTHETIC_COMPANIES)
        cins = {c.cin for c in companies}
        assert cins == {cin for _, cin, *_ in _SYNTHETIC_COMPANIES}
