"""CRUD for saved searches, plus /execute which runs a saved search
through the exact same filter engine POST /api/search/companies/advanced
uses (app.search.advanced_query) -- Phase 6 of the multi-source expansion,
see docs/multi_source_architecture.md.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    AdvancedSearchResponse,
    AdvancedSearchResultOut,
    CompanyOut,
    SavedSearchCreate,
    SavedSearchExecuteRequest,
    SavedSearchOut,
    SavedSearchUpdate,
)
from app.db.base import get_db
from app.models.saved_search import SavedSearch
from app.search.advanced_query import find_unknown_bucket, search_companies_advanced
from app.search.filter_registry import InvalidFilterConditionError, UnknownFilterFieldError
from app.search.filter_types import FilterNode, SortSpec, UnknownHandling

router = APIRouter(prefix="/api/saved-searches", tags=["saved-searches"])

_FilterNodeAdapter = TypeAdapter(FilterNode)


@router.post("", response_model=SavedSearchOut, status_code=201)
def create_saved_search(body: SavedSearchCreate, db: Session = Depends(get_db)) -> SavedSearchOut:
    saved_search = SavedSearch(
        name=body.name,
        created_by=body.created_by,
        country_scope=body.country_scope,
        source_scope=body.source_scope,
        # .model_dump(mode="json") so a date/enum value inside a condition
        # (e.g. BETWEEN [date, date]) round-trips through JSONB as plain
        # JSON, not a Python object psycopg would reject.
        filter_definition=body.filter_definition.model_dump(mode="json"),
        sort=[s.model_dump(mode="json") for s in body.sort],
        selected_fields=body.selected_fields,
    )
    db.add(saved_search)
    db.commit()
    return SavedSearchOut.model_validate(saved_search)


@router.get("", response_model=list[SavedSearchOut])
def list_saved_searches(
    created_by: str | None = Query(default=None, description="Filter to saved searches by this creator"),
    db: Session = Depends(get_db),
) -> list[SavedSearchOut]:
    stmt = select(SavedSearch).order_by(SavedSearch.created_at.desc())
    if created_by:
        stmt = stmt.where(SavedSearch.created_by == created_by)
    return [SavedSearchOut.model_validate(s) for s in db.scalars(stmt)]


@router.get("/{saved_search_id}", response_model=SavedSearchOut)
def get_saved_search(saved_search_id: uuid.UUID, db: Session = Depends(get_db)) -> SavedSearchOut:
    saved_search = db.get(SavedSearch, saved_search_id)
    if saved_search is None:
        raise HTTPException(status_code=404, detail="Saved search not found")
    return SavedSearchOut.model_validate(saved_search)


@router.patch("/{saved_search_id}", response_model=SavedSearchOut)
def update_saved_search(
    saved_search_id: uuid.UUID, body: SavedSearchUpdate, db: Session = Depends(get_db)
) -> SavedSearchOut:
    saved_search = db.get(SavedSearch, saved_search_id)
    if saved_search is None:
        raise HTTPException(status_code=404, detail="Saved search not found")

    updates = body.model_dump(exclude_unset=True)
    if "filter_definition" in updates:
        saved_search.filter_definition = body.filter_definition.model_dump(mode="json")
        del updates["filter_definition"]
    if "sort" in updates:
        saved_search.sort = [s.model_dump(mode="json") for s in body.sort]
        del updates["sort"]
    for field_name, value in updates.items():
        setattr(saved_search, field_name, value)

    db.commit()
    return SavedSearchOut.model_validate(saved_search)


@router.delete("/{saved_search_id}", status_code=204)
def delete_saved_search(saved_search_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    saved_search = db.get(SavedSearch, saved_search_id)
    if saved_search is None:
        raise HTTPException(status_code=404, detail="Saved search not found")
    db.delete(saved_search)
    db.commit()


@router.post("/{saved_search_id}/execute", response_model=AdvancedSearchResponse)
def execute_saved_search(
    saved_search_id: uuid.UUID, body: SavedSearchExecuteRequest, db: Session = Depends(get_db)
) -> AdvancedSearchResponse:
    """Runs filter_definition through the identical compile_where/
    evaluate_match_strength path a live POST /api/search/companies/advanced
    call uses (app.search.advanced_query) -- a saved search is never a
    separate, potentially-diverging execution path from an ad-hoc one."""
    saved_search = db.get(SavedSearch, saved_search_id)
    if saved_search is None:
        raise HTTPException(status_code=404, detail="Saved search not found")

    filter_node = _FilterNodeAdapter.validate_python(saved_search.filter_definition)
    sort = [SortSpec.model_validate(s) for s in saved_search.sort]

    try:
        matches = search_companies_advanced(
            db,
            filter_node,
            unknown_handling=body.unknown_handling,
            country_scope=saved_search.country_scope or None,
            source_scope=saved_search.source_scope or None,
            sort=sort,
            limit=body.limit,
            offset=body.offset,
        )
    except (UnknownFilterFieldError, InvalidFilterConditionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    results = [
        AdvancedSearchResultOut(company=CompanyOut.model_validate(m.company), match_strength=m.match_strength)
        for m in matches
    ]

    unknown_results: list[CompanyOut] = []
    if body.unknown_handling == UnknownHandling.INCLUDE_UNKNOWN_SEPARATELY:
        try:
            unknown_companies = find_unknown_bucket(
                db,
                filter_node,
                country_scope=saved_search.country_scope or None,
                source_scope=saved_search.source_scope or None,
                limit=body.limit,
                offset=body.offset,
            )
        except (UnknownFilterFieldError, InvalidFilterConditionError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        unknown_results = [CompanyOut.model_validate(c) for c in unknown_companies]

    return AdvancedSearchResponse(total_returned=len(results), results=results, unknown_results=unknown_results)
