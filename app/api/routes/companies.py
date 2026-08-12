import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    CompanyDetailOut,
    CompanyFinancialsOut,
    CompanyGSTRegistrationOut,
    CompanyOut,
    EvidenceOut,
)
from app.db.base import get_db
from app.ingestion.source_names import source_names_by_evidence
from app.models.company import Company
from app.models.evidence import Evidence
from app.models.financials import CompanyFinancials
from app.models.gst_registration import CompanyGSTRegistration

router = APIRouter(prefix="/api/companies", tags=["companies"])


def _get_company_or_404(company_id: uuid.UUID, db: Session) -> Company:
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.get("/{company_id}", response_model=CompanyDetailOut)
def get_company(company_id: uuid.UUID, db: Session = Depends(get_db)) -> CompanyDetailOut:
    company = _get_company_or_404(company_id, db)

    evidence_rows = list(db.scalars(select(Evidence).where(Evidence.company_id == company_id)))
    source_names = source_names_by_evidence(db, [e.id for e in evidence_rows])

    def _evidence_out(e: Evidence) -> EvidenceOut:
        out = EvidenceOut.model_validate(e)
        out.sources = source_names.get(e.id, [])
        return out

    company_out = CompanyOut.model_validate(company)
    return CompanyDetailOut(
        **company_out.model_dump(),
        evidence=[_evidence_out(e) for e in evidence_rows],
    )


@router.get("/{company_id}/financials", response_model=list[CompanyFinancialsOut])
def get_company_financials(company_id: uuid.UUID, db: Session = Depends(get_db)) -> list[CompanyFinancialsOut]:
    """Full financial-year history (see docs/ingestion.md#multi-valued-identifiers-and-time-series-financials) --
    not just the single most-recent-year snapshot mirrored onto Company."""
    _get_company_or_404(company_id, db)
    rows = list(
        db.scalars(
            select(CompanyFinancials)
            .where(CompanyFinancials.company_id == company_id)
            .order_by(CompanyFinancials.financial_year)
        )
    )
    return [CompanyFinancialsOut.model_validate(r) for r in rows]


@router.get("/{company_id}/gst-registrations", response_model=list[CompanyGSTRegistrationOut])
def get_company_gst_registrations(
    company_id: uuid.UUID, db: Session = Depends(get_db)
) -> list[CompanyGSTRegistrationOut]:
    """All GST registrations for this company -- a company may hold more than
    one (one per state of operation); Company.gstin is only a denormalized
    snapshot of whichever is_primary=True. See
    docs/ingestion.md#multi-valued-identifiers-and-time-series-financials."""
    _get_company_or_404(company_id, db)
    rows = list(
        db.scalars(
            select(CompanyGSTRegistration)
            .where(CompanyGSTRegistration.company_id == company_id)
            .order_by(CompanyGSTRegistration.is_primary.desc(), CompanyGSTRegistration.registered_state)
        )
    )
    return [CompanyGSTRegistrationOut.model_validate(r) for r in rows]
