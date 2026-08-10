"""Compiles a FilterGroup (filter_types.py) into a SQLAlchemy WHERE clause,
and separately evaluates a per-company, per-node MatchStrength.

Two passes, deliberately kept apart:

1. `compile_where()` builds one SQL expression executed once by Postgres.
   For range-capable numeric fields it uses *overlap* semantics (a company
   whose estimated range merely touches the target passes) -- exactly
   query.py's existing pattern, generalized. This means the SQL WHERE
   clause alone never distinguishes "definitely satisfies" from "might
   satisfy"; it only ever excludes companies that could not possibly
   satisfy the filter (including ones with no value at all for the
   field -- NULL propagation excludes them from ordinary AND/OR branches,
   which is exactly "never treat NULL as zero", not a workaround for it).

2. `evaluate_match_strength()` re-derives, per returned row, whether that
   row's match was DEFINITE, POSSIBLE, or UNKNOWN -- an application-level
   re-evaluation against the same company object already loaded from the
   query in (1), no second query. This mirrors query.py's
   range_match_is_definite() split (WHERE clause vs. definiteness
   annotation) but generalizes it to arbitrary fields/operators/AND/OR/NOT
   using three-valued (Kleene) logic: UNKNOWN never forces a NO/false
   result on its own, it just carries no information into the combination.
"""

from __future__ import annotations

import enum
from datetime import date
from typing import Any

from sqlalchemy import and_, func, not_, or_
from sqlalchemy.sql import ColumnElement

from app.core.logging import get_logger
from app.models.company import Company
from app.search.filter_registry import FieldSpec, company_attr, get_field_spec
from app.search.filter_types import FilterCondition, FilterDataType, FilterGroup, FilterOperator, MatchStrength

logger = get_logger(__name__)


def compile_where(node: FilterCondition | FilterGroup) -> ColumnElement:
    if isinstance(node, FilterCondition):
        return _compile_condition(node)
    if node.op == "AND":
        return and_(*[compile_where(c) for c in node.conditions])
    if node.op == "OR":
        return or_(*[compile_where(c) for c in node.conditions])
    if node.op == "NOT":
        return not_(compile_where(node.conditions[0]))
    raise ValueError(f"unknown group op {node.op!r}")  # unreachable: FilterGroup.op is a Literal


def _compile_condition(condition: FilterCondition) -> ColumnElement:
    spec = get_field_spec(condition.field)
    col = company_attr(spec.attr)

    if spec.range_min_attr and spec.range_max_attr:
        eff_min = func.coalesce(col, company_attr(spec.range_min_attr))
        eff_max = func.coalesce(col, company_attr(spec.range_max_attr))
        return _compile_numeric(condition, eff_min, eff_max)

    op = condition.operator
    if op == FilterOperator.EXISTS:
        return col.isnot(None)
    if op == FilterOperator.NOT_EXISTS:
        return col.is_(None)

    if spec.data_type == FilterDataType.STRING:
        return _compile_string(condition, col)
    if spec.data_type == FilterDataType.ENUM:
        return _compile_enum(condition, col)
    if spec.data_type == FilterDataType.BOOLEAN:
        return _compile_boolean(condition, col)
    if spec.data_type == FilterDataType.DATE:
        return _compile_date(condition, col)
    if spec.data_type == FilterDataType.NUMBER:
        return _compile_numeric(condition, col, col)
    raise ValueError(f"unhandled data_type {spec.data_type!r}")  # unreachable: enum is exhaustive above


def _compile_string(condition: FilterCondition, col) -> ColumnElement:
    op, value = condition.operator, condition.value
    if op == FilterOperator.EQ:
        return func.lower(col) == str(value).lower()
    if op == FilterOperator.NE:
        return and_(col.isnot(None), func.lower(col) != str(value).lower())
    if op == FilterOperator.CONTAINS:
        return col.ilike(f"%{_escape_like(value)}%")
    if op == FilterOperator.STARTS_WITH:
        return col.ilike(f"{_escape_like(value)}%")
    if op == FilterOperator.IN:
        return func.lower(col).in_([str(v).lower() for v in value])
    if op == FilterOperator.NOT_IN:
        return and_(col.isnot(None), func.lower(col).notin_([str(v).lower() for v in value]))
    raise ValueError(f"operator {op!r} not supported for STRING")  # unreachable: FilterCondition validates this


def _compile_enum(condition: FilterCondition, col) -> ColumnElement:
    op, value = condition.operator, condition.value
    if op == FilterOperator.EQ:
        return col == value
    if op == FilterOperator.NE:
        return and_(col.isnot(None), col != value)
    if op == FilterOperator.IN:
        return col.in_(value)
    if op == FilterOperator.NOT_IN:
        return and_(col.isnot(None), col.notin_(value))
    raise ValueError(f"operator {op!r} not supported for ENUM")  # unreachable: FilterCondition validates this


def _compile_boolean(condition: FilterCondition, col) -> ColumnElement:
    op, value = condition.operator, condition.value
    if op == FilterOperator.EQ:
        return col == bool(value)
    if op == FilterOperator.NE:
        return and_(col.isnot(None), col != bool(value))
    raise ValueError(f"operator {op!r} not supported for BOOLEAN")  # unreachable: FilterCondition validates this


def _compile_date(condition: FilterCondition, col) -> ColumnElement:
    op, value = condition.operator, condition.value
    if op == FilterOperator.EQ:
        return col == _to_date(value)
    if op == FilterOperator.NE:
        return and_(col.isnot(None), col != _to_date(value))
    if op == FilterOperator.GT:
        return col > _to_date(value)
    if op == FilterOperator.GTE:
        return col >= _to_date(value)
    if op == FilterOperator.LT:
        return col < _to_date(value)
    if op == FilterOperator.LTE:
        return col <= _to_date(value)
    if op == FilterOperator.BETWEEN:
        lo, hi = value
        return col.between(_to_date(lo), _to_date(hi))
    raise ValueError(f"operator {op!r} not supported for DATE")  # unreachable: FilterCondition validates this


def _compile_numeric(condition: FilterCondition, eff_min, eff_max) -> ColumnElement:
    op, value = condition.operator, condition.value
    if op == FilterOperator.EQ:
        return and_(eff_min <= value, eff_max >= value)
    if op == FilterOperator.NE:
        return not_(and_(eff_min <= value, eff_max >= value))
    if op == FilterOperator.GT:
        return eff_max > value
    if op == FilterOperator.GTE:
        return eff_max >= value
    if op == FilterOperator.LT:
        return eff_min < value
    if op == FilterOperator.LTE:
        return eff_min <= value
    if op == FilterOperator.BETWEEN:
        lo, hi = value
        return and_(eff_max >= lo, eff_min <= hi)
    if op == FilterOperator.IN:
        return or_(*[and_(eff_min <= v, eff_max >= v) for v in value])
    if op == FilterOperator.NOT_IN:
        return and_(*[not_(and_(eff_min <= v, eff_max >= v)) for v in value])
    raise ValueError(f"operator {op!r} not supported for NUMBER")  # unreachable: FilterCondition validates this


def _to_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _escape_like(value: Any) -> str:
    return str(value).replace("%", r"\%").replace("_", r"\_")


# --------------------------------------------------------------------------
# Match strength (application-level, post-query)
# --------------------------------------------------------------------------


class _Strength(str, enum.Enum):
    """Superset of MatchStrength used only inside this module's combination
    logic -- NO_MATCH never crosses the public evaluate_match_strength()
    boundary (see its docstring)."""

    DEFINITE = "definite"
    POSSIBLE = "possible"
    UNKNOWN = "unknown"
    NO_MATCH = "no_match"


def _and3(strengths: list[_Strength]) -> _Strength:
    if any(s == _Strength.NO_MATCH for s in strengths):
        return _Strength.NO_MATCH
    if any(s == _Strength.UNKNOWN for s in strengths):
        return _Strength.UNKNOWN
    if any(s == _Strength.POSSIBLE for s in strengths):
        return _Strength.POSSIBLE
    return _Strength.DEFINITE


def _or3(strengths: list[_Strength]) -> _Strength:
    if any(s == _Strength.DEFINITE for s in strengths):
        return _Strength.DEFINITE
    if any(s == _Strength.POSSIBLE for s in strengths):
        return _Strength.POSSIBLE
    if any(s == _Strength.UNKNOWN for s in strengths):
        return _Strength.UNKNOWN
    return _Strength.NO_MATCH


def _not3(s: _Strength) -> _Strength:
    return {
        _Strength.DEFINITE: _Strength.NO_MATCH,
        _Strength.NO_MATCH: _Strength.DEFINITE,
        _Strength.POSSIBLE: _Strength.POSSIBLE,
        _Strength.UNKNOWN: _Strength.UNKNOWN,
    }[s]


def evaluate_match_strength(node: FilterCondition | FilterGroup, company: Company) -> MatchStrength:
    """Public entry point. `company` must be a row that already passed
    compile_where(node) as part of the executed query -- this function does
    not itself decide inclusion/exclusion, only how strongly an already-
    included row matches.

    NO_MATCH is an internal combination state (see _Strength) that should be
    unreachable here for a row the SQL WHERE clause actually returned; if it
    ever occurs (e.g. a NOT-branch edge case, or a future SQL/Python
    evaluation drift) it's clamped to UNKNOWN and logged rather than raising
    or mislabeling a returned row as failing -- returning a wrong-but-safe
    answer beats crashing the search endpoint.
    """
    strength = _evaluate(node, company)
    if strength == _Strength.NO_MATCH:
        logger.warning(
            "match_strength_no_match_on_returned_row",
            extra={"extra_fields": {"company_id": str(company.id)}},
        )
        return MatchStrength.UNKNOWN
    return MatchStrength(strength.value)


def _evaluate(node: FilterCondition | FilterGroup, company: Company) -> _Strength:
    if isinstance(node, FilterCondition):
        return _leaf_strength(node, company)
    if node.op == "AND":
        return _and3([_evaluate(c, company) for c in node.conditions])
    if node.op == "OR":
        return _or3([_evaluate(c, company) for c in node.conditions])
    if node.op == "NOT":
        return _not3(_evaluate(node.conditions[0], company))
    raise ValueError(f"unknown group op {node.op!r}")  # unreachable: FilterGroup.op is a Literal


def _leaf_strength(condition: FilterCondition, company: Company) -> _Strength:
    spec = get_field_spec(condition.field)
    value = getattr(company, spec.attr)

    if spec.range_min_attr and spec.range_max_attr:
        range_min = getattr(company, spec.range_min_attr)
        range_max = getattr(company, spec.range_max_attr)
        eff_min = value if value is not None else range_min
        eff_max = value if value is not None else range_max
        if eff_min is None and eff_max is None:
            if condition.operator == FilterOperator.NOT_EXISTS:
                return _Strength.DEFINITE
            if condition.operator == FilterOperator.EXISTS:
                return _Strength.NO_MATCH
            return _Strength.UNKNOWN
        return _numeric_strength(condition, eff_min, eff_max, is_exact=value is not None)

    if value is None:
        if condition.operator == FilterOperator.NOT_EXISTS:
            return _Strength.DEFINITE
        if condition.operator == FilterOperator.EXISTS:
            return _Strength.NO_MATCH
        return _Strength.UNKNOWN

    return _exact_strength(condition, value)


def _exact_strength(condition: FilterCondition, value: Any) -> _Strength:
    op, target, dt = condition.operator, condition.value, condition.data_type

    if op == FilterOperator.EXISTS:
        return _Strength.DEFINITE
    if op == FilterOperator.NOT_EXISTS:
        return _Strength.NO_MATCH

    def norm(v: Any) -> Any:
        return v.lower() if isinstance(v, str) else v

    matched: bool
    if op == FilterOperator.EQ:
        matched = norm(value) == norm(target)
    elif op == FilterOperator.NE:
        matched = norm(value) != norm(target)
    elif op == FilterOperator.CONTAINS:
        matched = isinstance(value, str) and str(target).lower() in value.lower()
    elif op == FilterOperator.STARTS_WITH:
        matched = isinstance(value, str) and value.lower().startswith(str(target).lower())
    elif op == FilterOperator.IN:
        matched = norm(value) in [norm(v) for v in target]
    elif op == FilterOperator.NOT_IN:
        matched = norm(value) not in [norm(v) for v in target]
    elif op in (FilterOperator.GT, FilterOperator.GTE, FilterOperator.LT, FilterOperator.LTE):
        parsed_target = _to_date(target) if dt == FilterDataType.DATE else target
        matched = _compare_ordered(op, value, parsed_target)
    elif op == FilterOperator.BETWEEN:
        lo, hi = target
        if dt == FilterDataType.DATE:
            lo, hi = _to_date(lo), _to_date(hi)
        matched = lo <= value <= hi
    else:
        raise ValueError(f"unhandled operator {op!r}")  # unreachable: FilterCondition validates this
    return _Strength.DEFINITE if matched else _Strength.NO_MATCH


def _compare_ordered(op: FilterOperator, a: Any, b: Any) -> bool:
    if op == FilterOperator.GT:
        return a > b
    if op == FilterOperator.GTE:
        return a >= b
    if op == FilterOperator.LT:
        return a < b
    return a <= b  # LTE


def _numeric_strength(condition: FilterCondition, eff_min: float, eff_max: float, *, is_exact: bool) -> _Strength:
    op, target = condition.operator, condition.value

    if op == FilterOperator.IN:
        return _or3([_numeric_strength_single(FilterOperator.EQ, v, eff_min, eff_max, is_exact) for v in target])
    if op == FilterOperator.NOT_IN:
        return _and3([_numeric_strength_single(FilterOperator.NE, v, eff_min, eff_max, is_exact) for v in target])
    return _numeric_strength_single(op, target, eff_min, eff_max, is_exact)


def _numeric_strength_single(op: FilterOperator, target: Any, eff_min: float, eff_max: float, is_exact: bool) -> _Strength:
    if is_exact:
        v = eff_min  # eff_min == eff_max for an exact (non-range) value
        if op == FilterOperator.EQ:
            matched = v == target
        elif op == FilterOperator.NE:
            matched = v != target
        elif op == FilterOperator.GT:
            matched = v > target
        elif op == FilterOperator.GTE:
            matched = v >= target
        elif op == FilterOperator.LT:
            matched = v < target
        elif op == FilterOperator.LTE:
            matched = v <= target
        elif op == FilterOperator.BETWEEN:
            lo, hi = target
            matched = lo <= v <= hi
        else:
            raise ValueError(f"unhandled operator {op!r}")  # unreachable: FilterCondition validates this
        return _Strength.DEFINITE if matched else _Strength.NO_MATCH

    # eff_min/eff_max come from an estimated range, not a single known value.
    if op == FilterOperator.EQ:
        if eff_min == eff_max == target:
            return _Strength.DEFINITE
        if eff_min <= target <= eff_max:
            return _Strength.POSSIBLE
        return _Strength.NO_MATCH
    if op == FilterOperator.NE:
        if eff_min == eff_max == target:
            return _Strength.NO_MATCH
        if eff_min <= target <= eff_max:
            return _Strength.POSSIBLE
        return _Strength.DEFINITE
    if op == FilterOperator.GT:
        if eff_min > target:
            return _Strength.DEFINITE
        if eff_max > target:
            return _Strength.POSSIBLE
        return _Strength.NO_MATCH
    if op == FilterOperator.GTE:
        if eff_min >= target:
            return _Strength.DEFINITE
        if eff_max >= target:
            return _Strength.POSSIBLE
        return _Strength.NO_MATCH
    if op == FilterOperator.LT:
        if eff_max < target:
            return _Strength.DEFINITE
        if eff_min < target:
            return _Strength.POSSIBLE
        return _Strength.NO_MATCH
    if op == FilterOperator.LTE:
        if eff_max <= target:
            return _Strength.DEFINITE
        if eff_min <= target:
            return _Strength.POSSIBLE
        return _Strength.NO_MATCH
    if op == FilterOperator.BETWEEN:
        lo, hi = target
        if eff_min >= lo and eff_max <= hi:
            return _Strength.DEFINITE
        if eff_max >= lo and eff_min <= hi:
            return _Strength.POSSIBLE
        return _Strength.NO_MATCH
    raise ValueError(f"unhandled operator {op!r}")  # unreachable: FilterCondition validates this
