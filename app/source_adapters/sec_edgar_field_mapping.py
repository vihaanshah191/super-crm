"""Documented, data-driven mapping between SEC EDGAR's confirmed
`submissions/CIK##########.json` and `api/xbrl/companyfacts/CIK##########.json`
response fields and the internal canonical field keys
`SecEdgarAdapter.normalize()` consumes.

Field names and the revenue-selection logic below were built directly
against a real, live response (Apple Inc., CIK 0000320193, fetched
2026-08-14) -- nothing here is a guess. See docs/sec_edgar_data_access.md
for the full verification.

Revenue
-------
SEC EDGAR does not have one single "revenue" field -- filers tag their
income-statement line items with US-GAAP XBRL concept names, which have
changed over time as accounting standards evolved. Two concepts are tried,
in order:
  1. `RevenueFromContractWithCustomerExcludingAssessedTax` -- the modern
     ASC 606 revenue-recognition tag, used by most filers since ~2018.
  2. `Revenues` -- the older, pre-ASC-606 tag, used by filers who haven't
     re-tagged historical data or use simpler revenue recognition.
Both were directly observed as present (with real numeric data) in the
verification response. Only `fp="FY"` (full fiscal year) entries are
considered -- quarterly entries share the same concept name and would
otherwise be picked up as if they were annual figures. Only the `USD`
unit is read; non-USD filers are out of scope for this batch (documented
limitation, not silently mishandled -- see docs/sec_edgar_data_access.md).
"""

from __future__ import annotations

from dataclasses import dataclass

# canonical field -> submissions.json top-level key (dotted for nested).
# Structural fields (addresses, filings) are handled directly in the
# adapter since they aren't simple renames.
SEC_EDGAR_SUBMISSIONS_FIELD_MAP: dict[str, str] = {
    "name": "legal_name",
    "sicDescription": "industry",
    "sic": "sic_code",
    "entityType": "company_type",
    "website": "website",
    "phone": "public_phone",
}

STRUCTURAL_FIELDS: frozenset[str] = frozenset({"addresses", "filings", "cik", "tickers", "exchanges"})

CANONICAL_FIELDS: tuple[str, ...] = (
    "cik",
    "legal_name",
    "industry",
    "sic_code",
    "company_type",
    "website",
    "public_phone",
    "registered_address",
    "city",
    "state",
    "postal_code",
    "country_code",
)

REQUIRED_CANONICAL_FIELDS: frozenset[str] = frozenset({"cik"})

# In priority order -- see module docstring.
_REVENUE_CONCEPTS: tuple[str, ...] = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
)


def map_submissions(submissions: dict[str, object]) -> dict[str, object]:
    """Translate SEC EDGAR's submissions.json top-level field names to
    canonical field keys. Unmapped/unrecognized fields are dropped
    silently -- never a crash."""
    mapped: dict[str, object] = {}
    for key, value in submissions.items():
        canonical = SEC_EDGAR_SUBMISSIONS_FIELD_MAP.get(key)
        if canonical is not None and value not in (None, ""):
            mapped[canonical] = value
    return mapped


@dataclass(frozen=True)
class AnnualRevenue:
    value_usd: int
    fiscal_year: int
    concept: str
    form: str
    accession_number: str | None


def select_annual_revenue(company_facts: dict | None) -> AnnualRevenue | None:
    """Pick the most recent full-fiscal-year USD revenue figure from a
    companyfacts.json payload, or None if no usable revenue concept is
    present (e.g. the filer has no XBRL data at all, or reports in a
    non-USD currency -- both real, expected cases, not an error)."""
    if not company_facts:
        return None
    us_gaap = (company_facts.get("facts") or {}).get("us-gaap") or {}

    for concept in _REVENUE_CONCEPTS:
        concept_data = us_gaap.get(concept)
        if not concept_data:
            continue
        usd_points = (concept_data.get("units") or {}).get("USD") or []
        annual_points = [p for p in usd_points if p.get("fp") == "FY" and p.get("val") is not None and p.get("fy")]
        if not annual_points:
            continue
        latest = max(annual_points, key=lambda p: (p.get("end") or "", p.get("filed") or ""))
        return AnnualRevenue(
            value_usd=int(latest["val"]),
            fiscal_year=int(latest["fy"]),
            concept=concept,
            form=str(latest.get("form") or ""),
            accession_number=latest.get("accn"),
        )
    return None


@dataclass(frozen=True)
class FieldComparison:
    unknown_fields: list[str]
    matched_canonical_fields: list[str]
    missing_canonical_fields: list[str]
    missing_required_fields: list[str]


def compare_fields(observed_field_names: list[str]) -> FieldComparison:
    """Diff a live/observed submissions.json key list against what this
    adapter expects -- used by `python -m app.cli.sec_edgar_lookup` to
    report unknown/missing fields without touching normalize()/parse()."""
    observed = set(observed_field_names)
    known = set(SEC_EDGAR_SUBMISSIONS_FIELD_MAP.keys()) | STRUCTURAL_FIELDS

    unknown = sorted(observed - known)
    matched_canonical = {
        SEC_EDGAR_SUBMISSIONS_FIELD_MAP[k] for k in observed if k in SEC_EDGAR_SUBMISSIONS_FIELD_MAP
    }
    missing_canonical = sorted(set(CANONICAL_FIELDS) - matched_canonical)
    missing_required = sorted(REQUIRED_CANONICAL_FIELDS - matched_canonical)

    return FieldComparison(
        unknown_fields=unknown,
        matched_canonical_fields=sorted(matched_canonical),
        missing_canonical_fields=missing_canonical,
        missing_required_fields=missing_required,
    )
