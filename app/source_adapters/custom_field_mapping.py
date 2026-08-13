"""Generic, user-declared mapping from an arbitrary CSV/JSON source's own
column names to Super CRM's canonical field vocabulary -- the mechanism
Section 4 of docs/multi_source_architecture.md asks for ("Company Name" ->
legal_name, "CIN Number" -> cin, "State" -> state, "Turnover" ->
annual_revenue_inr, ...).

Unlike mca_field_mapping.py/filesure_field_mapping.py (which encode one
specific provider's *observed* field names), this module has no fixed
source-side vocabulary at all -- the mapping is supplied at import time by
whoever is adding the custom source. What IS fixed is the canonical side:
only field names CustomFileAdapter.normalize() actually knows how to turn
into an ObservationDraft are accepted (CANONICAL_FIELD_TYPES), and each one
has a declared data type used to validate incoming values before they're
trusted -- "Do not blindly trust custom mappings" (task spec, Section 4).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ingestion.normalization.dates import parse_flexible_date
from app.source_adapters.government_dataset_adapter import clean_numeric_string

# canonical field -> expected data type ("string" | "number" | "date" | "boolean").
# Deliberately a subset of every Company column -- only fields this adapter's
# normalize() has a real projection for (see custom_file_adapter.py).
CANONICAL_FIELD_TYPES: dict[str, str] = {
    "legal_name": "string",
    "cin": "string",
    "gstin": "string",
    "website": "string",
    "public_phone": "string",
    "public_email": "string",
    "registered_address": "string",
    "city": "string",
    "state": "string",
    "postal_code": "string",
    "country": "string",
    "country_code": "string",  # ISO 3166-1 alpha-2, e.g. "IN" -- see app.search.advanced_query's country_scope
    "industry": "string",
    "sub_industry": "string",
    "company_type": "string",
    "company_category": "string",
    "products": "string",  # comma-separated list, e.g. "Widgets, Fasteners"
    "services": "string",  # comma-separated list, same convention as products
    "incorporation_date": "date",
    "employee_count": "number",
    "employee_range_min": "number",
    "employee_range_max": "number",
    "annual_revenue_inr": "number",
    "revenue_range_min_inr": "number",
    "revenue_range_max_inr": "number",
    "revenue_year": "number",
    "export_status": "boolean",
}

SUPPORTED_CANONICAL_FIELDS: frozenset[str] = frozenset(CANONICAL_FIELD_TYPES)

# At least one of these must be mapped, or entity resolution has no name to
# create/match a company with -- see app/ingestion/pipeline.py's
# _create_company_stub(), which refuses to create a company with no name.
NAME_BEARING_FIELDS: frozenset[str] = frozenset({"legal_name"})


@dataclass(frozen=True)
class MappingIssue:
    severity: str  # "error" | "warning"
    message: str


def validate_field_mapping(mapping: dict[str, str]) -> list[MappingIssue]:
    """Static validation of the mapping itself (source_field -> canonical_field),
    independent of any actual row data -- see value_matches_type() for the
    per-row, per-value check. Never raises; callers decide whether
    error-severity issues should block an import (app.cli.import_custom_source
    does; a hypothetical future admin-UI preview might just display them)."""
    issues: list[MappingIssue] = []
    if not mapping:
        issues.append(MappingIssue("error", "Field mapping is empty -- nothing would be imported."))
        return issues

    seen_canonical: dict[str, str] = {}
    for source_field, canonical_field in mapping.items():
        if canonical_field not in SUPPORTED_CANONICAL_FIELDS:
            issues.append(
                MappingIssue(
                    "error",
                    f"'{source_field}' -> '{canonical_field}': unknown canonical field. "
                    f"Supported: {sorted(SUPPORTED_CANONICAL_FIELDS)}",
                )
            )
            continue
        if canonical_field in seen_canonical:
            issues.append(
                MappingIssue(
                    "warning",
                    f"Both '{seen_canonical[canonical_field]}' and '{source_field}' map to "
                    f"'{canonical_field}' -- only one will be used per row (last mapping key wins).",
                )
            )
        seen_canonical[canonical_field] = source_field

    if not NAME_BEARING_FIELDS & set(seen_canonical):
        issues.append(
            MappingIssue(
                "error",
                "No source field is mapped to 'legal_name' -- entity resolution cannot create or "
                "match a company without a name.",
            )
        )
    return issues


def value_matches_type(raw: str, data_type: str) -> bool:
    """Per-value type check -- a column can be declared 'number' in the
    mapping but still contain a bad cell in any given row; this is checked
    row by row, not just once for the mapping shape (validate_field_mapping
    above)."""
    if raw is None or raw.strip() == "":
        return True  # an empty cell is "unknown", not "wrong type" -- never rejected here
    if data_type == "number":
        return clean_numeric_string(raw) is not None
    if data_type == "date":
        return parse_flexible_date(raw) is not None
    if data_type == "boolean":
        return raw.strip().lower() in {"true", "false", "yes", "no", "1", "0"}
    return True  # string: anything is valid


def map_row(row: dict[str, object], mapping: dict[str, str]) -> dict[str, str]:
    """Project one raw row onto canonical field names, dropping unmapped
    source columns and unknown canonical targets. Values are stringified
    only -- type-specific parsing/validation happens in
    CustomFileAdapter.normalize(), not here."""
    mapped: dict[str, str] = {}
    for source_field, canonical_field in mapping.items():
        if canonical_field not in SUPPORTED_CANONICAL_FIELDS:
            continue
        raw_value = row.get(source_field)
        if raw_value is None:
            continue
        mapped[canonical_field] = str(raw_value).strip()
    return mapped
