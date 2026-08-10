"""Documented, data-driven mapping between MCA Company Master Data's external
column names (as published by data.gov.in, in either its CSV or JSON API
form) and the internal canonical field keys GovernmentDatasetAdapter.normalize()
consumes.

Why this exists: we have NOT yet verified the live dataset's actual column
names against a real API response (see docs/mca_data_access.md -- that
requires DATA_GOV_IN_API_KEY, which is not configured). Hardcoding exact
column-name strings throughout the adapter would mean any mismatch between
our assumption and the real schema silently drops every field. Centralizing
the mapping here means:

  1. A newly observed real column-name variant (confirmed via
     `python -m app.cli.inspect_mca_schema` once a key exists) is a one-line
     addition here, not a change to parsing/normalization logic.
  2. An external column we don't recognize is simply ignored, never a crash --
     `map_external_fields()` drops unmapped keys rather than raising.
  3. `compare_fields()` gives inspect_mca_schema a precise, importable
     definition of "expected" to diff the live schema against.

This file is data + two small pure functions. It intentionally has no I/O,
no DB access, and does not import scrapling -- it's a plain lookup table.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Canonical internal field keys, in the order GovernmentDatasetAdapter.normalize()
# consumes them. "cin" is the only one validate() treats as required -- a row
# missing it fails validation rather than being ingested with a fabricated CIN.
CANONICAL_FIELDS: tuple[str, ...] = (
    "cin",
    "company_name",
    "company_status",
    "company_class",
    "company_category",
    "date_of_registration",
    "registered_state",
    "registered_office_address",
    "roc",
    "authorized_capital",
    "paidup_capital",
)

REQUIRED_CANONICAL_FIELDS: frozenset[str] = frozenset({"cin"})

# external column name (already upper-cased, whitespace-trimmed, and stripped
# of a trailing "(...)" annotation like "(for efiling)" -- see
# _clean_external_key()) -> canonical field key.
#
# Multiple external keys may map to the same canonical field: data.gov.in has
# republished this dataset under slightly different column names across its
# CSV and per-state/RoC mirror exports over the years. Every variant below
# was either present in the fixture this project shipped with, or is a
# documented historical alias -- none of it has been checked against a live
# API response yet.
MCA_EXTERNAL_FIELD_MAP: dict[str, str] = {
    "CIN": "cin",
    "COMPANY_NAME": "company_name",
    "COMPANY_STATUS": "company_status",
    "COMPANY_CLASS": "company_class",
    "COMPANY_CATEGORY": "company_category",
    "COMPANY_SUB_CATEGORY": "company_category",
    "DATE_OF_REGISTRATION": "date_of_registration",
    "DATE_OF_INCORPORATION": "date_of_registration",
    "REGISTERED_STATE": "registered_state",
    "REGISTERED_OFFICE_ADDRESS": "registered_office_address",
    "ROC": "roc",
    "REGISTRAR_OF_COMPANIES": "roc",
    "ROC_CODE": "roc",
    "AUTHORIZED_CAPITAL": "authorized_capital",
    "AUTHORIZED_CAP": "authorized_capital",
    "PAIDUP_CAPITAL": "paidup_capital",
    "PAID_UP_CAPITAL": "paidup_capital",
}


def _clean_external_key(raw_key: str) -> str:
    """Normalize an external column name for lookup: strip a trailing
    parenthetical annotation (e.g. "COMPANY_STATUS(for efiling)" ->
    "COMPANY_STATUS"), trim whitespace, upper-case."""
    return raw_key.split("(")[0].strip().upper()


def map_external_fields(row: dict[str, object]) -> dict[str, object]:
    """Translate one raw record's external column names to canonical field
    keys. Unmapped external columns are dropped silently -- an unrecognized
    column must never crash ingestion; see docs/adding_a_source.md."""
    mapped: dict[str, object] = {}
    for raw_key, value in row.items():
        canonical = MCA_EXTERNAL_FIELD_MAP.get(_clean_external_key(str(raw_key)))
        if canonical is not None:
            mapped[canonical] = value
    return mapped


@dataclass(frozen=True)
class FieldComparison:
    """Result of comparing a real (or fixture) dataset's observed external
    column names against MCA_EXTERNAL_FIELD_MAP."""

    unknown_external_fields: list[str] = field(default_factory=list)
    matched_canonical_fields: list[str] = field(default_factory=list)
    missing_canonical_fields: list[str] = field(default_factory=list)
    missing_required_fields: list[str] = field(default_factory=list)


def compare_fields(observed_external_field_names: list[str]) -> FieldComparison:
    """Diff a live/observed field-name list against what this adapter expects.

    unknown_external_fields: columns present in the data but not in our map
      (not an error -- just something a human should look at and possibly
      add an alias for).
    missing_canonical_fields: canonical fields we know how to use, for which
      NONE of the observed columns map -- i.e. that piece of data would
      silently be unavailable from this dataset export.
    missing_required_fields: subset of the above that are load-bearing
      (currently just "cin") -- if this is non-empty, rows from this export
      cannot pass validate() at all.
    """
    cleaned_observed = {_clean_external_key(name) for name in observed_external_field_names}
    known_external = set(MCA_EXTERNAL_FIELD_MAP.keys())

    unknown = sorted(cleaned_observed - known_external)

    matched_canonical: set[str] = set()
    for external_key in cleaned_observed:
        canonical = MCA_EXTERNAL_FIELD_MAP.get(external_key)
        if canonical is not None:
            matched_canonical.add(canonical)

    missing_canonical = sorted(set(CANONICAL_FIELDS) - matched_canonical)
    missing_required = sorted(REQUIRED_CANONICAL_FIELDS - matched_canonical)

    return FieldComparison(
        unknown_external_fields=unknown,
        matched_canonical_fields=sorted(matched_canonical),
        missing_canonical_fields=missing_canonical,
        missing_required_fields=missing_required,
    )
