"""Maps generic filter field names to actual Company/Evidence columns.

This is the one place that knows which SQL columns back which filter field
-- filter_compiler.py never references `Company.<column>` directly, so
adding a filterable field is "add one entry here" rather than touching the
compiler. Numeric fields that have a companion estimated-range pair
(employee_count/employee_range_min/max, revenue/revenue_range_min/max)
declare `range_min_attr`/`range_max_attr` so the compiler can reuse the
existing overlap-match semantics from app/search/query.py's
range_match_is_definite() instead of a second, divergent implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.company import Company
from app.search.filter_types import OPERATORS_BY_DATA_TYPE, FilterCondition, FilterDataType, FilterOperator


@dataclass(frozen=True)
class FieldSpec:
    data_type: FilterDataType
    attr: str  # Company attribute name holding the exact/primary value
    range_min_attr: str | None = None
    range_max_attr: str | None = None
    allowed_operators: frozenset[FilterOperator] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.allowed_operators:
            object.__setattr__(self, "allowed_operators", OPERATORS_BY_DATA_TYPE[self.data_type])


# field name (as used in FilterCondition.field) -> FieldSpec.
#
# Deliberately NOT every Company column -- only ones there's a real search
# use case for today. Adding more is additive (no migration, no compiler
# change needed).
FIELD_REGISTRY: dict[str, FieldSpec] = {
    "country": FieldSpec(FilterDataType.STRING, "country"),
    "country_code": FieldSpec(FilterDataType.STRING, "country_code"),
    "state": FieldSpec(FilterDataType.STRING, "state"),
    "city": FieldSpec(FilterDataType.STRING, "city"),
    "postal_code": FieldSpec(FilterDataType.STRING, "postal_code"),
    "industry": FieldSpec(FilterDataType.STRING, "industry"),
    "sub_industry": FieldSpec(FilterDataType.STRING, "sub_industry"),
    "legal_name": FieldSpec(FilterDataType.STRING, "legal_name"),
    "canonical_name": FieldSpec(FilterDataType.STRING, "canonical_name"),
    "website": FieldSpec(FilterDataType.STRING, "website_domain"),
    "cin": FieldSpec(FilterDataType.STRING, "cin"),
    "gstin": FieldSpec(FilterDataType.STRING, "gstin"),
    "company_category": FieldSpec(FilterDataType.ENUM, "company_category"),
    "company_type": FieldSpec(FilterDataType.STRING, "company_type"),
    "export_status": FieldSpec(FilterDataType.BOOLEAN, "export_status"),
    "incorporation_date": FieldSpec(FilterDataType.DATE, "incorporation_date"),
    "last_verified_at": FieldSpec(
        FilterDataType.DATE,
        "last_verified_at",
        allowed_operators=frozenset(
            {FilterOperator.GT, FilterOperator.GTE, FilterOperator.LT, FilterOperator.LTE, FilterOperator.EXISTS, FilterOperator.NOT_EXISTS}
        ),
    ),
    "confidence": FieldSpec(
        FilterDataType.NUMBER,
        "confidence",
        allowed_operators=frozenset(
            {FilterOperator.GT, FilterOperator.GTE, FilterOperator.LT, FilterOperator.LTE, FilterOperator.EQ, FilterOperator.NE}
        ),
    ),
    "employees": FieldSpec(
        FilterDataType.NUMBER, "employee_count", "employee_range_min", "employee_range_max"
    ),
    "revenue_inr": FieldSpec(
        FilterDataType.NUMBER, "annual_revenue_inr", "revenue_range_min_inr", "revenue_range_max_inr"
    ),
    "revenue_year": FieldSpec(FilterDataType.NUMBER, "revenue_year"),
}


class UnknownFilterFieldError(ValueError):
    pass


class InvalidFilterConditionError(ValueError):
    """Raised when a FilterCondition's data_type or operator doesn't match
    what FIELD_REGISTRY declares for condition.field. Pydantic (see
    FilterCondition._check_operator_valid_for_type) only checks the
    operator against what's valid for the *claimed* data_type in general --
    a client can still claim data_type="string" for a field the registry
    says is NUMBER, or use an operator this specific field disallows (e.g.
    last_verified_at only allows ordering operators, not EQ/NE). This is
    the second, field-specific check re-validated server-side."""


def get_field_spec(field_name: str) -> FieldSpec:
    spec = FIELD_REGISTRY.get(field_name)
    if spec is None:
        raise UnknownFilterFieldError(
            f"'{field_name}' is not a filterable field. Known fields: {sorted(FIELD_REGISTRY)}"
        )
    return spec


def validate_condition(condition: FilterCondition) -> FieldSpec:
    """Resolve condition.field's FieldSpec and cross-check it against what
    the client actually sent -- data_type must match the registry's
    declared type, and operator must be within this field's own
    allowed_operators (a stricter, per-field subset of what's merely valid
    for the data_type in general -- see FieldSpec.allowed_operators). Called
    once per condition by filter_compiler._compile_condition() before any
    type-specific compilation runs, so a mismatch is rejected with a clear
    422-mappable error instead of crashing inside _compile_numeric/
    _compile_string with an unhandled ValueError, or silently bypassing a
    field's intended operator restriction."""
    spec = get_field_spec(condition.field)
    if condition.data_type != spec.data_type:
        raise InvalidFilterConditionError(
            f"field '{condition.field}' has data_type {spec.data_type.value!r}, not {condition.data_type.value!r}"
        )
    if condition.operator not in spec.allowed_operators:
        raise InvalidFilterConditionError(
            f"operator {condition.operator.value!r} is not allowed for field '{condition.field}' "
            f"(allowed: {sorted(o.value for o in spec.allowed_operators)})"
        )
    return spec


def company_attr(attr_name: str):
    """SQLAlchemy InstrumentedAttribute for a Company column by name."""
    return getattr(Company, attr_name)
