"""Deterministic-identifiers-first entity matching.

Scoring is a plain, explainable rule table -- not an ML model -- per the
project's requirement that the confidence/matching system be deterministic
and inspectable. Key invariant, enforced by construction: name similarity
alone (however high) never reaches AUTO_MATCH_THRESHOLD. Only statutory
identifiers (CIN, GSTIN) can do that on their own; everything else -- website
domain, email domain, name + corroborating location -- lands in the "review"
band and produces an EntityMatchCandidate instead of a silent merge.
"""

from dataclasses import dataclass, field

from app.ingestion.entity_resolution.fuzzy import name_similarity

AUTO_MATCH_THRESHOLD = 0.90
REVIEW_THRESHOLD = 0.50

_CIN_SCORE = 1.0
_GSTIN_SCORE = 0.95
_DOMAIN_SCORE = 0.75
_EMAIL_DOMAIN_SCORE = 0.60
# Note: a shared postal/PIN code is a weak locality signal -- Indian PIN codes
# cover entire industrial estates with hundreds of unrelated companies -- so
# even "exact name + matching postal code" stays in the review band. Only
# statutory identifiers (CIN/GSTIN) are strong enough to auto-match alone.
_NAME_EXACT_WITH_LOCATION_SCORE = 0.75
_NAME_EXACT_ALONE_SCORE = 0.55
_NAME_FUZZY_WITH_LOCATION_SCORE = 0.65
_NAME_FUZZY_ALONE_SCORE = 0.30

_NAME_EXACT_THRESHOLD = 0.98
_NAME_FUZZY_THRESHOLD = 0.85


@dataclass(frozen=True)
class IdentitySignals:
    cin: str | None = None
    gstin: str | None = None
    website_domain: str | None = None
    normalized_name: str | None = None
    state: str | None = None
    postal_code: str | None = None
    public_email_domain: str | None = None


@dataclass(frozen=True)
class MatchResult:
    score: float
    decision: str  # "auto_match" | "review" | "no_match"
    matched_signals: dict = field(default_factory=dict)


def score_match(incoming: IdentitySignals, candidate: IdentitySignals) -> MatchResult:
    score = 0.0
    signals: dict[str, bool | float] = {}

    if incoming.cin and candidate.cin:
        matched = incoming.cin.strip().upper() == candidate.cin.strip().upper()
        signals["cin_match"] = matched
        if matched:
            score = max(score, _CIN_SCORE)

    if incoming.gstin and candidate.gstin:
        matched = incoming.gstin.strip().upper() == candidate.gstin.strip().upper()
        signals["gstin_match"] = matched
        if matched:
            score = max(score, _GSTIN_SCORE)

    if incoming.website_domain and candidate.website_domain:
        matched = incoming.website_domain.strip().lower() == candidate.website_domain.strip().lower()
        signals["website_domain_match"] = matched
        if matched:
            score = max(score, _DOMAIN_SCORE)

    if incoming.public_email_domain and candidate.public_email_domain:
        matched = incoming.public_email_domain.strip().lower() == candidate.public_email_domain.strip().lower()
        signals["email_domain_match"] = matched
        if matched:
            score = max(score, _EMAIL_DOMAIN_SCORE)

    if incoming.normalized_name and candidate.normalized_name:
        similarity = name_similarity(incoming.normalized_name, candidate.normalized_name)
        signals["name_similarity"] = round(similarity, 3)

        state_match = bool(
            incoming.state and candidate.state and incoming.state.strip().lower() == candidate.state.strip().lower()
        )
        postal_match = bool(
            incoming.postal_code and candidate.postal_code and incoming.postal_code == candidate.postal_code
        )
        if state_match:
            signals["state_match"] = True
        if postal_match:
            signals["postal_code_match"] = True
        has_location_corroboration = state_match or postal_match

        if similarity >= _NAME_EXACT_THRESHOLD and has_location_corroboration:
            score = max(score, _NAME_EXACT_WITH_LOCATION_SCORE)
        elif similarity >= _NAME_EXACT_THRESHOLD:
            score = max(score, _NAME_EXACT_ALONE_SCORE)
        elif similarity >= _NAME_FUZZY_THRESHOLD and has_location_corroboration:
            score = max(score, _NAME_FUZZY_WITH_LOCATION_SCORE)
        elif similarity >= _NAME_FUZZY_THRESHOLD:
            score = max(score, _NAME_FUZZY_ALONE_SCORE)

    if score >= AUTO_MATCH_THRESHOLD:
        decision = "auto_match"
    elif score >= REVIEW_THRESHOLD:
        decision = "review"
    else:
        decision = "no_match"

    return MatchResult(score=round(score, 3), decision=decision, matched_signals=signals)
