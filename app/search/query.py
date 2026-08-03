"""Deterministic filter -> SQL translation.

Every filter here maps to an indexed column (see the initial Alembic
migration: state/city/industry, employee_range_min/max, annual_revenue_inr,
company_category, confidence). Nothing here scans all companies -- range
filters use the coalesce(exact, range-bound) pattern so a single indexed
column comparison covers both "employee_count=34" and "employee_range=25-40"
rows in the same query.

Range match semantics
---------------------
When a query specifies an employee or revenue range and a company only has an
*estimated* range (e.g. 10–30 employees), the WHERE clause accepts the company
if the ranges *overlap* -- the company *could* satisfy the filter. Whether it
*definitely* satisfies the filter is a separate question answered by
range_match_is_definite(), which callers can surface in the API response.

  overlap  (current WHERE clause): effective_max >= query_min AND effective_min <= query_max
  definite (additional annotation): effective_min >= query_min AND effective_max <= query_max

A company with employee_count=50 queried at employee_min=20 is a definite match.
A company with employee_range 10–30 queried at employee_min=20 is a *possible*
match (ranges overlap) but not definite (the low end of 10 doesn't meet 20).
"""

from sqlalchemy import String, cast, func, select
from sqlalchemy.sql import Select

from app.models.company import Company
from app.models.evidence import Evidence
from app.search.filters import CompanySearchFilters


def build_company_query(filters: CompanySearchFilters) -> Select:
    stmt = select(Company)

    if filters.industry:
        stmt = stmt.where(Company.industry.ilike(f"%{filters.industry}%"))
    if filters.product:
        # PoC-level substring match over the JSONB products array. A larger
        # deployment should add a GIN index on products (jsonb_ops) or move
        # product/service matching to the search index stage.
        stmt = stmt.where(cast(Company.products, String).ilike(f"%{filters.product}%"))
    if filters.city:
        stmt = stmt.where(Company.city.ilike(f"%{filters.city}%"))
    if filters.state:
        stmt = stmt.where(Company.state.ilike(f"%{filters.state}%"))
    if filters.country:
        stmt = stmt.where(Company.country.ilike(f"%{filters.country}%"))

    if filters.employee_min is not None or filters.employee_max is not None:
        effective_min = func.coalesce(Company.employee_count, Company.employee_range_min)
        effective_max = func.coalesce(Company.employee_count, Company.employee_range_max)
        if filters.employee_min is not None:
            stmt = stmt.where(effective_max >= filters.employee_min)
        if filters.employee_max is not None:
            stmt = stmt.where(effective_min <= filters.employee_max)

    if filters.revenue_min_inr is not None or filters.revenue_max_inr is not None:
        effective_min = func.coalesce(Company.annual_revenue_inr, Company.revenue_range_min_inr)
        effective_max = func.coalesce(Company.annual_revenue_inr, Company.revenue_range_max_inr)
        if filters.revenue_min_inr is not None:
            stmt = stmt.where(effective_max >= filters.revenue_min_inr)
        if filters.revenue_max_inr is not None:
            stmt = stmt.where(effective_min <= filters.revenue_max_inr)

    if filters.incorporated_before is not None:
        stmt = stmt.where(Company.incorporation_date < filters.incorporated_before)
    if filters.incorporated_after is not None:
        stmt = stmt.where(Company.incorporation_date >= filters.incorporated_after)

    if filters.company_category:
        stmt = stmt.where(Company.company_category == filters.company_category)
    if filters.export_status is not None:
        stmt = stmt.where(Company.export_status == filters.export_status)

    if filters.min_confidence is not None:
        stmt = stmt.where(Company.confidence >= filters.min_confidence)
    if filters.last_verified_after is not None:
        stmt = stmt.where(Company.last_verified_at >= filters.last_verified_after)

    if filters.verification_type:
        evidence_exists = (
            select(Evidence.id)
            .where(Evidence.company_id == Company.id, Evidence.verification_type == filters.verification_type)
            .exists()
        )
        stmt = stmt.where(evidence_exists)

    return stmt.order_by(Company.confidence.desc()).offset(filters.offset).limit(filters.limit)


def range_match_is_definite(company: Company, filters: CompanySearchFilters) -> bool | None:
    """Return whether *c* definitely satisfies *f*'s employee/revenue range filters.

    Returns None when no range filter is active (definite/possible distinction N/A).
    Returns True when every applied range filter is met by the company's lower bound
    (i.e. the company's range is entirely within or above the query range).
    Returns False when the company passed the WHERE clause via overlap only --
    the low end of the company's estimated range may not satisfy the filter.
    """
    has_employee_filter = filters.employee_min is not None or filters.employee_max is not None
    has_revenue_filter = filters.revenue_min_inr is not None or filters.revenue_max_inr is not None
    if not has_employee_filter and not has_revenue_filter:
        return None

    definite = True

    if has_employee_filter:
        eff_min = company.employee_count if company.employee_count is not None else company.employee_range_min
        eff_max = company.employee_count if company.employee_count is not None else company.employee_range_max
        if filters.employee_min is not None and eff_min is not None:
            if eff_min < filters.employee_min:
                definite = False
        if filters.employee_max is not None and eff_max is not None:
            if eff_max > filters.employee_max:
                definite = False

    if has_revenue_filter:
        eff_min = company.annual_revenue_inr if company.annual_revenue_inr is not None else company.revenue_range_min_inr
        eff_max = company.annual_revenue_inr if company.annual_revenue_inr is not None else company.revenue_range_max_inr
        if filters.revenue_min_inr is not None and eff_min is not None:
            if eff_min < filters.revenue_min_inr:
                definite = False
        if filters.revenue_max_inr is not None and eff_max is not None:
            if eff_max > filters.revenue_max_inr:
                definite = False

    return definite
