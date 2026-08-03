from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import CompanySearchResponse, CompanySearchResultOut
from app.db.base import get_db
from app.search.filters import CompanySearchFilters
from app.search.query import build_company_query, range_match_is_definite

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("/companies", response_model=CompanySearchResponse)
def search_companies(filters: CompanySearchFilters, db: Session = Depends(get_db)) -> CompanySearchResponse:
    """Execute a structured filter set deterministically against the
    canonical company table. An LLM may have produced `filters` from a
    natural-language query upstream of this endpoint -- it plays no role in
    deciding which companies match.

    Each result includes `match_is_definite` (True/False/null):
      - null: no employee or revenue range filter was active.
      - True: the company's known low bound already satisfies all range filters --
        even its least-favorable data point clears the bar.
      - False: ranges overlap (company passes the WHERE clause) but the company's
        estimated low bound is below a filter threshold -- the match is *possible*,
        not guaranteed. Callers can surface this uncertainty to end users.
    """
    stmt = build_company_query(filters)
    companies = list(db.scalars(stmt))
    results = []
    for company in companies:
        out = CompanySearchResultOut.model_validate(company)
        out.match_is_definite = range_match_is_definite(company, filters)
        results.append(out)
    return CompanySearchResponse(total_returned=len(results), results=results)
