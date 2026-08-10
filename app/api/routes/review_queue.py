import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import CompanyOut, EntityMatchCandidateDetailOut, ReviewDecisionIn
from app.db.base import get_db
from app.ingestion.pipeline import confirm_match, reject_match
from app.models.company import Company
from app.models.match_candidate import EntityMatchCandidate

router = APIRouter(prefix="/api/review-queue", tags=["review-queue"])


@router.get("", response_model=list[EntityMatchCandidateDetailOut])
def list_pending_matches(db: Session = Depends(get_db)) -> list[EntityMatchCandidateDetailOut]:
    """Ambiguous entity-resolution decisions awaiting human review -- see
    docs/entity_resolution.md. Never auto-merged; a human must confirm or
    reject each one."""
    candidates = list(
        db.scalars(
            select(EntityMatchCandidate)
            .where(EntityMatchCandidate.status == "pending")
            .order_by(EntityMatchCandidate.created_at)
        )
    )
    results = []
    for c in candidates:
        candidate_company = db.get(Company, c.candidate_company_id) if c.candidate_company_id else None
        out = EntityMatchCandidateDetailOut.model_validate(c)
        out.candidate_company = CompanyOut.model_validate(candidate_company) if candidate_company else None
        results.append(out)
    return results


@router.post("/{candidate_id}/confirm", response_model=CompanyOut)
def confirm(candidate_id: uuid.UUID, body: ReviewDecisionIn, db: Session = Depends(get_db)) -> CompanyOut:
    try:
        company = confirm_match(db, candidate_id, body.reviewed_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return CompanyOut.model_validate(company)


@router.post("/{candidate_id}/reject", status_code=204)
def reject(candidate_id: uuid.UUID, body: ReviewDecisionIn, db: Session = Depends(get_db)) -> None:
    try:
        reject_match(db, candidate_id, body.reviewed_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
