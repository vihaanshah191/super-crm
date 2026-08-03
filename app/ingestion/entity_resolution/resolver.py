"""Candidate generation + resolution orchestration. This is the only entity-
resolution module that touches the database -- matcher.py and fuzzy.py stay
pure and independently testable.

Candidate generation deliberately avoids scanning every company: it uses
exact-equality lookups on indexed identifier columns (cin, gstin,
website_domain) plus a bounded pg_trgm similarity query on normalized_name
(backed by the GIN trigram index from the initial migration), never a full
table scan.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.ingestion.entity_resolution.matcher import IdentitySignals, MatchResult, score_match
from app.models.company import Company
from app.models.match_candidate import EntityMatchCandidate

_TRIGRAM_SIMILARITY_THRESHOLD = 0.3
_CANDIDATE_LIMIT = 10


@dataclass(frozen=True)
class ResolutionOutcome:
    decision: str  # "auto_match" | "review" | "no_match"
    company: Company | None
    match_result: MatchResult | None


def signals_from_company(company: Company) -> IdentitySignals:
    email_domain = company.public_email.split("@")[-1].lower() if company.public_email and "@" in company.public_email else None
    return IdentitySignals(
        cin=company.cin,
        gstin=company.gstin,
        website_domain=company.website_domain,
        normalized_name=company.normalized_name,
        state=company.state,
        postal_code=company.postal_code,
        public_email_domain=email_domain,
    )


def find_candidates(db: Session, signals: IdentitySignals, limit: int = _CANDIDATE_LIMIT) -> list[Company]:
    candidates: dict[uuid.UUID, Company] = {}

    exact_clauses = []
    if signals.cin:
        exact_clauses.append(Company.cin == signals.cin)
    if signals.gstin:
        exact_clauses.append(Company.gstin == signals.gstin)
    if signals.website_domain:
        exact_clauses.append(Company.website_domain == signals.website_domain)

    if exact_clauses:
        for company in db.scalars(select(Company).where(or_(*exact_clauses)).limit(limit)):
            candidates[company.id] = company

    if signals.normalized_name:
        similarity_expr = func.similarity(Company.normalized_name, signals.normalized_name)
        trigram_stmt = (
            select(Company)
            .where(similarity_expr > _TRIGRAM_SIMILARITY_THRESHOLD)
            .order_by(similarity_expr.desc())
            .limit(limit)
        )
        for company in db.scalars(trigram_stmt):
            candidates[company.id] = company

    return list(candidates.values())


def resolve(
    db: Session,
    observation_id: uuid.UUID,
    signals: IdentitySignals,
    incoming_payload: dict,
) -> ResolutionOutcome:
    """Find the best matching existing company for `signals`, or flag an
    ambiguous match for review. Never merges on name similarity alone --
    see matcher.score_match for the scoring rules."""
    candidates = find_candidates(db, signals)

    best: tuple[Company, MatchResult] | None = None
    for candidate in candidates:
        result = score_match(signals, signals_from_company(candidate))
        if best is None or result.score > best[1].score:
            best = (candidate, result)

    if best is None:
        return ResolutionOutcome(decision="no_match", company=None, match_result=None)

    company, result = best

    if result.decision == "auto_match":
        return ResolutionOutcome(decision="auto_match", company=company, match_result=result)

    if result.decision == "review":
        db.add(
            EntityMatchCandidate(
                observation_id=observation_id,
                candidate_company_id=company.id,
                incoming_payload=incoming_payload,
                match_score=result.score,
                matched_signals=result.matched_signals,
                status="pending",
            )
        )
        return ResolutionOutcome(decision="review", company=None, match_result=result)

    return ResolutionOutcome(decision="no_match", company=None, match_result=result)
