from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import CompanyOut, CompanySearchResponse
from app.db.base import get_db
from app.search.filters import CompanySearchFilters
from app.search.query import build_company_query

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("/companies", response_model=CompanySearchResponse)
def search_companies(filters: CompanySearchFilters, db: Session = Depends(get_db)) -> CompanySearchResponse:
    """Execute a structured filter set deterministically against the
    canonical company table. An LLM may have produced `filters` from a
    natural-language query upstream of this endpoint -- it plays no role in
    deciding which companies match."""
    stmt = build_company_query(filters)
    companies = list(db.scalars(stmt))
    return CompanySearchResponse(
        total_returned=len(companies),
        results=[CompanyOut.model_validate(c) for c in companies],
    )
