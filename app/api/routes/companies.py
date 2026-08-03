import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import CompanyDetailOut, CompanyOut, EvidenceOut
from app.db.base import get_db
from app.models.company import Company
from app.models.evidence import Evidence

router = APIRouter(prefix="/api/companies", tags=["companies"])


@router.get("/{company_id}", response_model=CompanyDetailOut)
def get_company(company_id: uuid.UUID, db: Session = Depends(get_db)) -> CompanyDetailOut:
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")

    evidence_rows = list(db.scalars(select(Evidence).where(Evidence.company_id == company_id)))

    company_out = CompanyOut.model_validate(company)
    return CompanyDetailOut(
        **company_out.model_dump(),
        evidence=[EvidenceOut.model_validate(e) for e in evidence_rows],
    )
