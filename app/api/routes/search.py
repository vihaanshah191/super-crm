from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import (
    AdvancedSearchRequest,
    AdvancedSearchResponse,
    AdvancedSearchResultOut,
    CompanyOut,
    CompanySearchResponse,
    CompanySearchResultOut,
)
from app.db.base import get_db
from app.ingestion.source_names import source_names_by_company
from app.search.advanced_query import find_unknown_bucket, search_companies_advanced
from app.search.filter_registry import InvalidFilterConditionError, UnknownFilterFieldError
from app.search.filters import CompanySearchFilters
from app.search.filter_types import UnknownHandling
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
    source_names = source_names_by_company(db, [c.id for c in companies])
    results = []
    for company in companies:
        out = CompanySearchResultOut.model_validate(company)
        out.match_is_definite = range_match_is_definite(company, filters)
        out.sources = source_names.get(company.id, [])
        results.append(out)
    return CompanySearchResponse(total_returned=len(results), results=results)


@router.post("/companies/advanced", response_model=AdvancedSearchResponse)
def search_companies_advanced_route(
    request: AdvancedSearchRequest, db: Session = Depends(get_db)
) -> AdvancedSearchResponse:
    """Generic field/operator/value filter engine (AND/OR/NOT over an
    arbitrary field set, see app.search.filter_types), as opposed to the
    fixed shorthand `/companies` above. Every returned company carries a
    `match_strength` (definite/possible/unknown -- see
    app.search.filter_compiler) instead of the single boolean
    `match_is_definite` the flat endpoint returns, since an arbitrary
    filter tree can combine multiple range-backed fields at once.

    `unknown_handling=include_unknown_separately` additionally populates
    `unknown_results` with companies excluded from `results` purely for
    lack of data on a filtered field (never because a known value actually
    contradicts the filter) -- see find_unknown_bucket()'s docstring for
    the (documented, not silent) cases this doesn't cover.
    """
    try:
        matches = search_companies_advanced(
            db,
            request.filter,
            unknown_handling=request.unknown_handling,
            country_scope=request.country_scope or None,
            source_scope=request.source_scope or None,
            sort=request.sort,
            limit=request.limit,
            offset=request.offset,
        )
    except (UnknownFilterFieldError, InvalidFilterConditionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    source_names = source_names_by_company(db, [m.company.id for m in matches])
    results = [
        AdvancedSearchResultOut(
            company=CompanyOut.model_validate(m.company),
            match_strength=m.match_strength,
            sources=source_names.get(m.company.id, []),
        )
        for m in matches
    ]

    unknown_results: list[CompanyOut] = []
    if request.unknown_handling == UnknownHandling.INCLUDE_UNKNOWN_SEPARATELY:
        try:
            unknown_companies = find_unknown_bucket(
                db,
                request.filter,
                country_scope=request.country_scope or None,
                source_scope=request.source_scope or None,
                limit=request.limit,
                offset=request.offset,
            )
        except (UnknownFilterFieldError, InvalidFilterConditionError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        unknown_results = [CompanyOut.model_validate(c) for c in unknown_companies]

    return AdvancedSearchResponse(total_returned=len(results), results=results, unknown_results=unknown_results)
