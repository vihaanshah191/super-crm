"""Documented, data-driven mapping between FileSure's confirmed
`GET /v1/companies/{cin}` response fields (`data.masterData.companyData`)
and the internal canonical field keys `FileSureAdapter.normalize()` consumes.

Unlike `mca_field_mapping.py` (which hedges against an unverified live
schema with many historical aliases), this mapping is built directly from a
real, confirmed response FileSure's own developer-portal documentation
embeds as a worked example -- see docs/filesure_data_access.md for the full
response and how it was obtained. It intentionally maps ONLY fields that
have been directly observed; nothing here is a guess.

Financial fields (revenue/turnover/profit) are deliberately NOT mapped here:
no confirmed schema for `/v1/companies/{cin}/extractions` (the endpoint that
would carry them) was found during research. See
FileSureAdapter._normalize_financials() for where that mapping will go once
a real extractions response has been observed -- see the live-sandbox
verification section of docs/filesure_data_access.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Canonical internal field keys FileSureAdapter.normalize() knows how to
# project onto ObservationDrafts. "cin" is the only one validate() treats as
# required.
CANONICAL_FIELDS: tuple[str, ...] = (
    "cin",
    "company_name",
    "company_status",
    "class_of_company",
    "company_category",
    "date_of_incorporation",
    "roc",
    "authorized_capital",
    "paidup_capital",
    "pan",
)

REQUIRED_CANONICAL_FIELDS: frozenset[str] = frozenset({"cin"})

# FileSure `companyData`/`commonData` field name -> canonical field key.
# Two generations of confirmation:
#   1. FileSure's own documented example (docs portal, Swiggy Limited) --
#      "cin", "companyName", "companyStatus", "rocCode", "paidupCapital".
#   2. A live sandbox call against that same CIN on 2026-08-06 (see
#      docs/filesure_data_access.md, "Live sandbox verification") -- which
#      showed real schema drift from #1: "cin"/"companyName"/"companyStatus"
#      are NOT inside companyData live (cin/company are top-level on `data`,
#      handled directly in FileSureAdapter.parse(); companyStatus lives in
#      a sibling "commonData" object FileSureAdapter merges in before
#      calling map_company_fields()), and paidUpCapital/rocName are live
#      variants of paidupCapital/rocCode with different casing/naming.
# Both generations' field names are kept as aliases below rather than
# replacing #1 with #2, since a single live sample doesn't prove #1 never
# occurs (FileSure's response may vary by company type/history).
FILESURE_COMPANY_FIELD_MAP: dict[str, str] = {
    "cin": "cin",
    "companyName": "company_name",
    "companyStatus": "company_status",
    "status": "company_status",  # commonData alias, confirmed live
    "classOfCompany": "class_of_company",
    "companyCategory": "company_category",
    "dateOfIncorporation": "date_of_incorporation",
    "rocCode": "roc",
    "rocName": "roc",  # confirmed live (companyData)
    "ROCName": "roc",  # confirmed live (commonData) -- same value as rocName in the one sample seen
    "authorisedCapital": "authorized_capital",
    "paidupCapital": "paidup_capital",
    "paidUpCapital": "paidup_capital",  # confirmed live casing variant
    "pan": "pan",
}

# Confirmed but NOT mapped to a canonical field -- registered address is
# structural (an array of address objects), handled directly in
# FileSureAdapter since it isn't a simple rename. Documented here so
# compare_fields() below can still recognize it as "known, not unmapped."
KNOWN_STRUCTURAL_FIELDS: frozenset[str] = frozenset({"MCAMDSCompanyAddress"})


def map_company_fields(company_data: dict[str, object]) -> dict[str, object]:
    """Translate FileSure's companyData field names to canonical field keys.
    Unmapped/unrecognized fields are dropped silently -- never a crash."""
    mapped: dict[str, object] = {}
    for key, value in company_data.items():
        canonical = FILESURE_COMPANY_FIELD_MAP.get(key)
        if canonical is not None:
            mapped[canonical] = value
    return mapped


@dataclass(frozen=True)
class FieldComparison:
    unknown_fields: list[str] = field(default_factory=list)
    matched_canonical_fields: list[str] = field(default_factory=list)
    missing_canonical_fields: list[str] = field(default_factory=list)
    missing_required_fields: list[str] = field(default_factory=list)


def compare_fields(observed_field_names: list[str]) -> FieldComparison:
    """Diff a live/observed companyData key list against what this adapter
    expects -- used by `python -m app.cli.filesure_lookup` to report
    unknown/missing fields without touching normalize()/parse() logic."""
    observed = set(observed_field_names)
    known = set(FILESURE_COMPANY_FIELD_MAP.keys()) | KNOWN_STRUCTURAL_FIELDS

    unknown = sorted(observed - known)
    matched_canonical = {
        FILESURE_COMPANY_FIELD_MAP[k] for k in observed if k in FILESURE_COMPANY_FIELD_MAP
    }
    missing_canonical = sorted(set(CANONICAL_FIELDS) - matched_canonical)
    missing_required = sorted(REQUIRED_CANONICAL_FIELDS - matched_canonical)

    return FieldComparison(
        unknown_fields=unknown,
        matched_canonical_fields=sorted(matched_canonical),
        missing_canonical_fields=missing_canonical,
        missing_required_fields=missing_required,
    )
