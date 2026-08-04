from datetime import datetime, timedelta, timezone

from app.ingestion.confidence.engine import ObservationForConfidence, compute_field_confidence


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestConfidenceEngine:
    def test_no_observations_yields_unknown(self):
        result = compute_field_confidence([])
        assert result.confidence == 0.0
        assert result.verification_type == "unknown"

    def test_single_verified_government_source_is_high_confidence(self):
        result = compute_field_confidence(
            [ObservationForConfidence(95, "verified", "maharashtra", NOW)], now=NOW
        )
        assert result.verification_type == "verified"
        assert result.confidence > 0.85

    def test_single_observed_website_source_is_moderate_confidence(self):
        result = compute_field_confidence(
            [ObservationForConfidence(40, "observed", "maharashtra", NOW)], now=NOW
        )
        assert result.verification_type == "observed"
        assert 0.0 < result.confidence < 0.6

    def test_agreement_across_sources_increases_confidence(self):
        single = compute_field_confidence(
            [ObservationForConfidence(95, "verified", "maharashtra", NOW)], now=NOW
        )
        combined = compute_field_confidence(
            [
                ObservationForConfidence(95, "verified", "maharashtra", NOW),
                ObservationForConfidence(40, "observed", "maharashtra", NOW),
            ],
            now=NOW,
        )
        assert combined.confidence >= single.confidence

    def test_conflicting_values_reduce_confidence_but_keep_correct_verification_type(self):
        result = compute_field_confidence(
            [
                ObservationForConfidence(95, "verified", "maharashtra", NOW),
                ObservationForConfidence(40, "observed", "gujarat", NOW),
            ],
            now=NOW,
        )
        no_conflict = compute_field_confidence(
            [ObservationForConfidence(95, "verified", "maharashtra", NOW)], now=NOW
        )
        assert result.confidence < no_conflict.confidence
        # The winning (majority) value is still the verified one -- an
        # estimate must never be presented as verified.
        assert result.verification_type == "verified"
        assert result.value == "maharashtra"

    def test_estimated_value_is_never_labeled_verified(self):
        result = compute_field_confidence(
            [ObservationForConfidence(60, "estimated", "34", NOW)], now=NOW
        )
        assert result.verification_type == "estimated"

    def test_stale_observation_has_lower_confidence_than_fresh(self):
        fresh = compute_field_confidence(
            [ObservationForConfidence(90, "verified", "x", NOW)], now=NOW
        )
        stale = compute_field_confidence(
            [ObservationForConfidence(90, "verified", "x", NOW - timedelta(days=900))], now=NOW
        )
        assert stale.confidence < fresh.confidence

    def test_conflict_tie_break_is_deterministic_regardless_of_input_order(self):
        """A 1-1 count tie between a VERIFIED and an OBSERVED value must always
        resolve to the stronger (verified) observation -- not whichever happened
        to be listed/returned first. Regression test: compute_field_confidence()
        used to break count ties via Counter.most_common() insertion order, which
        depended on the caller's list order (and, in production, undefined SQL
        row order) rather than observation strength."""
        forward = compute_field_confidence(
            [
                ObservationForConfidence(95, "verified", "maharashtra", NOW),
                ObservationForConfidence(40, "observed", "gujarat", NOW),
            ],
            now=NOW,
        )
        reversed_input = compute_field_confidence(
            [
                ObservationForConfidence(40, "observed", "gujarat", NOW),
                ObservationForConfidence(95, "verified", "maharashtra", NOW),
            ],
            now=NOW,
        )
        assert forward.value == "maharashtra"
        assert reversed_input.value == "maharashtra"
        assert forward.confidence == reversed_input.confidence

    def test_confidence_is_bounded_between_zero_and_one(self):
        result = compute_field_confidence(
            [
                ObservationForConfidence(100, "verified", "x", NOW),
                ObservationForConfidence(100, "verified", "x", NOW),
                ObservationForConfidence(100, "verified", "x", NOW),
                ObservationForConfidence(100, "verified", "x", NOW),
                ObservationForConfidence(100, "verified", "x", NOW),
            ],
            now=NOW,
        )
        assert 0.0 <= result.confidence <= 1.0
