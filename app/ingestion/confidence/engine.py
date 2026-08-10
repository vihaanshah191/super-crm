"""Deterministic, rules/weights-based confidence engine.

Explicitly NOT an ML model -- every score is a sum of explainable terms, and
compute_field_confidence() returns the full breakdown so an API/UI can show
"why" a confidence value exists. Replacing this with a learned model later is
possible without changing its interface (inputs: a list of observations for
one company+field; output: a score + explanation).

Factors considered, per the product spec:
  - source reliability (Source.reliability_weight, 0-100)
  - verification type (verified > observed > estimated > unknown)
  - freshness (exponential decay by age)
  - number of independent corroborating sources
  - agreement between sources (conflicting values reduce confidence)

Invariant: the rolled-up verification_type is only ever as strong as the
observations that actually agree on the winning value -- a VERIFIED
observation for a *different* value never lets an OBSERVED-only value get
labeled VERIFIED.
"""

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

VERIFICATION_TYPE_WEIGHT = {
    "verified": 1.0,
    "observed": 0.7,
    "estimated": 0.5,
    "unknown": 0.1,
}

_FRESHNESS_HALF_LIFE_DAYS = 365.0
_MAX_INDEPENDENT_SOURCE_BONUS = 0.15
_PER_EXTRA_SOURCE_BONUS = 0.05
_MAX_CONFLICT_PENALTY = 0.30


@dataclass(frozen=True)
class ObservationForConfidence:
    source_reliability_weight: int  # 0-100, from Source.reliability_weight
    verification_type: str
    normalized_value: str | None
    collected_at: datetime


@dataclass(frozen=True)
class ConfidenceResult:
    confidence: float
    verification_type: str
    value: str | None = None
    explanation: dict = field(default_factory=dict)


def _freshness(collected_at: datetime, now: datetime) -> float:
    age_days = max((now - collected_at).total_seconds() / 86400.0, 0.0)
    return math.exp(-age_days / _FRESHNESS_HALF_LIFE_DAYS)


def compute_field_confidence(
    observations: list[ObservationForConfidence],
    *,
    now: datetime | None = None,
) -> ConfidenceResult:
    if not observations:
        return ConfidenceResult(confidence=0.0, verification_type="unknown", explanation={"reason": "no_observations"})

    now = now or datetime.now(timezone.utc)

    value_key = lambda v: (v or "").strip().lower()  # noqa: E731

    def _obs_score(o: ObservationForConfidence) -> float:
        reliability = max(0.0, min(o.source_reliability_weight, 100)) / 100.0
        verification_weight = VERIFICATION_TYPE_WEIGHT.get(o.verification_type, VERIFICATION_TYPE_WEIGHT["unknown"])
        return reliability * verification_weight * _freshness(o.collected_at, now)

    value_counts = Counter(value_key(o.normalized_value) for o in observations)
    # Ties on raw count are broken by the strongest single observation backing that
    # value (highest reliability x verification x freshness), not by the accidental
    # order observations were passed in / returned from the DB. Without this, a 1-1
    # split between a VERIFIED and an OBSERVED value would pick a "winner" based on
    # undefined query row order rather than which observation actually deserves it.
    best_score_by_value: dict[str, float] = {}
    for o in observations:
        key = value_key(o.normalized_value)
        score = _obs_score(o)
        if key not in best_score_by_value or score > best_score_by_value[key]:
            best_score_by_value[key] = score
    majority_value = max(value_counts, key=lambda v: (value_counts[v], best_score_by_value[v]))
    majority_count = value_counts[majority_value]
    agreement_ratio = majority_count / len(observations)

    agreeing = [o for o in observations if value_key(o.normalized_value) == majority_value]

    per_obs_scores = [_obs_score(o) for o in agreeing]
    base_score = max(per_obs_scores) if per_obs_scores else 0.0
    independent_source_bonus = min(
        _MAX_INDEPENDENT_SOURCE_BONUS, _PER_EXTRA_SOURCE_BONUS * (len(agreeing) - 1)
    )
    conflict_penalty = (1.0 - agreement_ratio) * _MAX_CONFLICT_PENALTY

    confidence = max(0.0, min(1.0, base_score + independent_source_bonus - conflict_penalty))

    strongest = max(agreeing, key=lambda o: VERIFICATION_TYPE_WEIGHT.get(o.verification_type, 0))

    explanation = {
        "base_score": round(base_score, 3),
        "independent_source_bonus": round(independent_source_bonus, 3),
        "conflict_penalty": round(conflict_penalty, 3),
        "agreement_ratio": round(agreement_ratio, 3),
        "agreeing_observation_count": len(agreeing),
        "total_observation_count": len(observations),
        "source_reliability_weights": [o.source_reliability_weight for o in agreeing],
        "verification_types": [o.verification_type for o in agreeing],
    }

    return ConfidenceResult(
        confidence=round(confidence, 3),
        verification_type=strongest.verification_type,
        value=strongest.normalized_value,
        explanation=explanation,
    )
