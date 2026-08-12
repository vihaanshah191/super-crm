"""Orchestrates the generic filter engine (filter_types/filter_compiler)
into an executable search against Company, honoring UnknownHandling.

Kept separate from filter_compiler.py because this module runs queries
(needs a Session); filter_compiler.py stays pure/session-free so its
compilation and match-strength logic can be unit tested without a database.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.observation import RawObservation
from app.search.filter_compiler import compile_order_by, compile_where, evaluate_match_strength
from app.search.filter_registry import company_attr, get_field_spec
from app.search.filter_types import FilterCondition, FilterGroup, MatchStrength, SortSpec, UnknownHandling


@dataclass(frozen=True)
class AdvancedSearchResult:
    company: Company
    match_strength: MatchStrength


def _scope_clauses(country_scope: list[str] | None, source_scope: list[uuid.UUID] | None) -> list:
    """Additional narrowing clauses for a saved search's country_scope/
    source_scope (Phase 6) -- crisp include/exclude, not evidence-backed
    filters, so they never participate in DEFINITE/POSSIBLE/UNKNOWN
    match-strength evaluation the way a FilterCondition does."""
    clauses = []
    if country_scope:
        clauses.append(Company.country_code.in_(country_scope))
    if source_scope:
        clauses.append(
            select(RawObservation.id)
            .where(RawObservation.company_id == Company.id, RawObservation.source_id.in_(source_scope))
            .exists()
        )
    return clauses


def search_companies_advanced(
    db: Session,
    filter_node: FilterCondition | FilterGroup,
    *,
    unknown_handling: UnknownHandling = UnknownHandling.DEFINITE_AND_POSSIBLE,
    country_scope: list[str] | None = None,
    source_scope: list[uuid.UUID] | None = None,
    sort: list[SortSpec] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[AdvancedSearchResult]:
    """The ordinary WHERE clause (compile_where) already excludes companies
    with no value at all for a filtered field -- NULL never satisfies a
    comparison, so it's naturally never coerced to zero/false (Section 8).
    That means DEFINITE_AND_POSSIBLE (the default) needs no extra query:
    the compiled WHERE clause already *is* "definite or possible." Only
    DEFINITE_ONLY needs a post-filter pass, since "possible" rows still
    pass the SQL WHERE by design (they might match; see filter_compiler's
    module docstring).

    `country_scope`/`source_scope` are saved-search scoping, not filter
    conditions -- see _scope_clauses(). `sort` defaults to confidence
    descending (the pre-Phase-6 behavior) when not given."""
    where_clause = compile_where(filter_node)
    order_by = compile_order_by(sort) if sort else [Company.confidence.desc()]
    stmt = (
        select(Company)
        .where(where_clause, *_scope_clauses(country_scope, source_scope))
        .order_by(*order_by)
        .offset(offset)
        .limit(limit)
    )
    companies = list(db.scalars(stmt))

    results = [
        AdvancedSearchResult(company=c, match_strength=evaluate_match_strength(filter_node, c)) for c in companies
    ]

    if unknown_handling == UnknownHandling.DEFINITE_ONLY:
        results = [r for r in results if r.match_strength == MatchStrength.DEFINITE]

    return results


def find_unknown_bucket(
    db: Session,
    filter_node: FilterCondition | FilterGroup,
    *,
    country_scope: list[str] | None = None,
    source_scope: list[uuid.UUID] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Company]:
    """Companies excluded from the ordinary result set purely because they
    have no data at all for one of the filter's fields -- not because a
    known value actually contradicts the filter. Only queried when the
    caller explicitly asks for it (UnknownHandling.INCLUDE_UNKNOWN_SEPARATELY).

    Exact for a single condition or a flat AND of conditions (the common
    case: "state = X AND revenue >= Y", show unknown-revenue Maharashtra
    companies separately). For OR/NOT/nested-group trees, "unknown" isn't
    well-defined without three-valued logic pushed fully into SQL, so this
    deliberately returns an empty list rather than guessing at a plausible-
    looking but wrong bucket -- documented here, not silently wrong.
    """
    if isinstance(filter_node, FilterCondition):
        conditions = [filter_node]
    elif filter_node.op == "AND" and all(isinstance(c, FilterCondition) for c in filter_node.conditions):
        conditions = list(filter_node.conditions)
    else:
        return []

    null_clauses = [_field_null_clause(c.field) for c in conditions]
    # Each condition either holds (compile_where(c)) or its field is simply
    # unknown for this row (null_clause) -- and at least one condition's
    # field must actually be the unknown one, or this is just an ordinary
    # match already covered by search_companies_advanced().
    per_condition_clauses = [or_(null, compile_where(c)) for null, c in zip(null_clauses, conditions)]

    stmt = (
        select(Company)
        .where(and_(*per_condition_clauses), or_(*null_clauses), *_scope_clauses(country_scope, source_scope))
        .order_by(Company.confidence.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(stmt))


def _field_null_clause(field_name: str):
    spec = get_field_spec(field_name)
    col = company_attr(spec.attr)
    if spec.range_min_attr and spec.range_max_attr:
        return and_(
            col.is_(None),
            company_attr(spec.range_min_attr).is_(None),
            company_attr(spec.range_max_attr).is_(None),
        )
    return col.is_(None)
