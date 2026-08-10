"""Generic, serializable filter representation.

`CompanySearchFilters` (filters.py) stays as the flat, hand-typed shorthand
used by the original `/api/search/companies` endpoint -- it isn't replaced.
This module adds a second, more general representation for the same
underlying query capability: an arbitrary AND/OR/NOT tree of
(field, operator, value) conditions, driven by a field registry rather than
one hardcoded Python attribute per filter. It exists so:

  - the field set is extensible without a new Pydantic field + query.py
    branch per filter (see FIELD_REGISTRY),
  - operators are validated against what's actually valid for a field's
    data type (a CONTAINS on a date field is rejected at the API boundary,
    not silently ignored or turned into a 500),
  - the whole tree is plain Pydantic, so it round-trips to/from JSON for
    saved searches (see docs/multi_source_architecture.md, Phase 6) without
    any bespoke serialization code.

See filter_compiler.py for how a FilterGroup becomes a SQLAlchemy WHERE
clause and a per-result MatchStrength.
"""

from __future__ import annotations

import enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator


class FilterDataType(str, enum.Enum):
    STRING = "string"
    NUMBER = "number"
    DATE = "date"
    BOOLEAN = "boolean"
    ENUM = "enum"


class FilterOperator(str, enum.Enum):
    EQ = "="
    NE = "!="
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    IN = "IN"
    NOT_IN = "NOT_IN"
    CONTAINS = "CONTAINS"
    STARTS_WITH = "STARTS_WITH"
    EXISTS = "EXISTS"
    NOT_EXISTS = "NOT_EXISTS"
    BETWEEN = "BETWEEN"


# Which operators are meaningful for each data type. A field's own allowed
# set (FieldSpec.allowed_operators, filter_registry.py) is always a subset
# of this -- this is the ceiling, not a per-field default.
OPERATORS_BY_DATA_TYPE: dict[FilterDataType, frozenset[FilterOperator]] = {
    FilterDataType.STRING: frozenset(
        {
            FilterOperator.EQ,
            FilterOperator.NE,
            FilterOperator.CONTAINS,
            FilterOperator.STARTS_WITH,
            FilterOperator.IN,
            FilterOperator.NOT_IN,
            FilterOperator.EXISTS,
            FilterOperator.NOT_EXISTS,
        }
    ),
    FilterDataType.NUMBER: frozenset(
        {
            FilterOperator.EQ,
            FilterOperator.NE,
            FilterOperator.GT,
            FilterOperator.GTE,
            FilterOperator.LT,
            FilterOperator.LTE,
            FilterOperator.IN,
            FilterOperator.NOT_IN,
            FilterOperator.BETWEEN,
            FilterOperator.EXISTS,
            FilterOperator.NOT_EXISTS,
        }
    ),
    FilterDataType.DATE: frozenset(
        {
            FilterOperator.EQ,
            FilterOperator.NE,
            FilterOperator.GT,
            FilterOperator.GTE,
            FilterOperator.LT,
            FilterOperator.LTE,
            FilterOperator.BETWEEN,
            FilterOperator.EXISTS,
            FilterOperator.NOT_EXISTS,
        }
    ),
    FilterDataType.BOOLEAN: frozenset(
        {
            FilterOperator.EQ,
            FilterOperator.NE,
            FilterOperator.EXISTS,
            FilterOperator.NOT_EXISTS,
        }
    ),
    FilterDataType.ENUM: frozenset(
        {
            FilterOperator.EQ,
            FilterOperator.NE,
            FilterOperator.IN,
            FilterOperator.NOT_IN,
            FilterOperator.EXISTS,
            FilterOperator.NOT_EXISTS,
        }
    ),
}

_NO_VALUE_OPERATORS = frozenset({FilterOperator.EXISTS, FilterOperator.NOT_EXISTS})
_LIST_VALUE_OPERATORS = frozenset({FilterOperator.IN, FilterOperator.NOT_IN})


class FilterCondition(BaseModel):
    """One leaf condition: `field operator value`. `data_type` is supplied
    by the caller (not inferred) so validation is explicit and doesn't
    depend on guessing types from a raw JSON value -- see validate() in
    filter_compiler.py, which cross-checks this against FIELD_REGISTRY
    rather than trusting it blindly (field type AND operator-for-type are
    both re-validated server-side, since a client could claim any data_type
    it likes)."""

    field: str
    operator: FilterOperator
    data_type: FilterDataType
    value: object | None = None

    @model_validator(mode="after")
    def _check_operator_valid_for_type(self) -> "FilterCondition":
        allowed = OPERATORS_BY_DATA_TYPE[self.data_type]
        if self.operator not in allowed:
            raise ValueError(
                f"operator {self.operator.value!r} is not valid for data_type "
                f"{self.data_type.value!r} (allowed: {sorted(o.value for o in allowed)})"
            )
        if self.operator in _NO_VALUE_OPERATORS:
            if self.value is not None:
                raise ValueError(f"operator {self.operator.value!r} does not take a value")
        elif self.operator == FilterOperator.BETWEEN:
            if not (isinstance(self.value, (list, tuple)) and len(self.value) == 2):
                raise ValueError("BETWEEN requires a value of exactly [low, high]")
        elif self.operator in _LIST_VALUE_OPERATORS:
            if not isinstance(self.value, (list, tuple)) or len(self.value) == 0:
                raise ValueError(f"operator {self.operator.value!r} requires a non-empty list value")
        else:
            if self.value is None:
                raise ValueError(f"operator {self.operator.value!r} requires a value")
        return self


class FilterGroup(BaseModel):
    """A boolean combination of conditions and/or nested groups.

    `op="NOT"` negates its (single) child -- represented as a one-element
    `conditions` list rather than a separate schema, so the whole structure
    stays one recursive type that's trivial to serialize/deserialize (see
    module docstring)."""

    op: Literal["AND", "OR", "NOT"]
    conditions: list["FilterNode"] = Field(default_factory=list, min_length=1)

    @model_validator(mode="after")
    def _check_not_arity(self) -> "FilterGroup":
        if self.op == "NOT" and len(self.conditions) != 1:
            raise ValueError("NOT must have exactly one child condition/group")
        return self


FilterNode = Annotated[Union[FilterCondition, FilterGroup], Field(union_mode="left_to_right")]
FilterGroup.model_rebuild()


class MatchStrength(str, enum.Enum):
    """How confidently a company satisfies a filter.

    DEFINITE: every evidence-backed value/range involved is entirely
    consistent with the filter (the company's least-favorable known data
    point still clears the bar).
    POSSIBLE: the company's data overlaps the filter's target but isn't
    strictly contained by it (e.g. an estimated employee range straddles
    the threshold) -- it *could* match, not confirmed.
    UNKNOWN: the field has no value at all for this company. Never treated
    as a failure to match and never coerced to zero/false -- an UNKNOWN
    leaf simply contributes no information to AND/OR/NOT combination (see
    filter_compiler.py's three-valued combination logic).
    """

    DEFINITE = "definite"
    POSSIBLE = "possible"
    UNKNOWN = "unknown"


class UnknownHandling(str, enum.Enum):
    """How a search should treat companies whose filtered field(s) are
    UNKNOWN for every condition that would otherwise apply to them."""

    DEFINITE_ONLY = "definite_only"
    DEFINITE_AND_POSSIBLE = "definite_and_possible"  # default
    INCLUDE_UNKNOWN_SEPARATELY = "include_unknown_separately"
