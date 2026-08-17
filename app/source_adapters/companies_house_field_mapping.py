"""Documented, data-driven mapping between Companies House's confirmed
`GET /company/{company_number}` response fields and the internal canonical
field keys `CompaniesHouseAdapter.normalize()` consumes.

Field names below are taken directly from the official API reference
(developer-specs.company-information.service.gov.uk/companies-house-public-data-api/reference,
fetched 2026-08-14) -- nothing here is guessed.

SIC industry bucketing
-----------------------
Companies House returns only numeric UK SIC 2007 codes (`sic_codes`, e.g.
["62012", "62020"]) -- it does NOT return the human-readable description
text inline (that requires a separate, large SIC-code-to-description
lookup this project doesn't maintain). Rather than leave `industry`
permanently empty for every Companies House company, or invent
company-specific descriptions we have no source for, this module buckets a
SIC code to its official ONS SIC-2007 *section* (a small, stable,
officially-published set of ~21 top-level divisions, e.g. "10-33" is
Manufacturing) -- coarse, but genuinely useful for exactly the kind of
"Industry = Manufacturing" filter this product is built around, and it's
public, standardized classification structure, not a guess. The raw SIC
code(s) are preserved as-is on `sub_industry` so nothing is lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# canonical field -> Companies House JSON path (dotted for nested fields).
# Structural fields (registered_office_address, sic_codes, accounts,
# confirmation_statement) are handled directly in the adapter since they
# aren't simple renames; listed here so compare_fields() can still
# recognize them as "known."
COMPANIES_HOUSE_FIELD_MAP: dict[str, str] = {
    "company_name": "legal_name",
    "company_number": "company_number",
    "company_status": "company_status",
    "type": "company_type",
    "date_of_creation": "incorporation_date",
    "jurisdiction": "jurisdiction",
}

STRUCTURAL_FIELDS: frozenset[str] = frozenset(
    {
        "sic_codes",
        "registered_office_address",
        "accounts",
        "confirmation_statement",
        "has_charges",
        "has_insolvency_history",
    }
)

CANONICAL_FIELDS: tuple[str, ...] = (
    "company_number",
    "legal_name",
    "company_status",
    "company_type",
    "incorporation_date",
    "jurisdiction",
    "industry",
    "sub_industry",
    "registered_address",
    "city",
    "state",
    "postal_code",
    "country_code",
)

REQUIRED_CANONICAL_FIELDS: frozenset[str] = frozenset({"company_number"})

# ONS SIC 2007 section boundaries (official UK government classification --
# see https://www.ons.gov.uk/methodology/classificationsandstandards/ukstandardindustrialclassificationofeconomicactivities/uksic2007).
# (low, high, section label) -- first matching range wins. Deliberately
# coarse (21 sections, not the ~700 individual SIC codes) since that's the
# stable, small, official structure -- no per-code description text is
# invented.
_SIC_SECTIONS: list[tuple[int, int, str]] = [
    (1, 3, "Agriculture, Forestry and Fishing"),
    (5, 9, "Mining and Quarrying"),
    (10, 33, "Manufacturing"),
    (35, 35, "Electricity, Gas, Steam and Air Conditioning Supply"),
    (36, 39, "Water Supply, Sewerage and Waste Management"),
    (41, 43, "Construction"),
    (45, 47, "Wholesale and Retail Trade"),
    (49, 53, "Transportation and Storage"),
    (55, 56, "Accommodation and Food Service Activities"),
    (58, 63, "Information and Communication"),
    (64, 66, "Financial and Insurance Activities"),
    (68, 68, "Real Estate Activities"),
    (69, 75, "Professional, Scientific and Technical Activities"),
    (77, 82, "Administrative and Support Service Activities"),
    (84, 84, "Public Administration and Defence"),
    (85, 85, "Education"),
    (86, 88, "Human Health and Social Work Activities"),
    (90, 93, "Arts, Entertainment and Recreation"),
    (94, 96, "Other Service Activities"),
    (97, 98, "Activities of Households as Employers"),
    (99, 99, "Activities of Extraterritorial Organisations"),
]


def sic_code_to_section(sic_code: str) -> str | None:
    """First 2 digits of a SIC 2007 code -> its official section label, or
    None if the code doesn't parse as a 2+ digit number."""
    digits = "".join(ch for ch in sic_code[:2] if ch.isdigit())
    if len(digits) < 2:
        return None
    division = int(digits)
    for low, high, label in _SIC_SECTIONS:
        if low <= division <= high:
            return label
    return None


def map_company_profile(profile: dict[str, object]) -> dict[str, object]:
    """Translate Companies House's top-level profile field names to
    canonical field keys. Unmapped/unrecognized fields are dropped
    silently -- never a crash."""
    mapped: dict[str, object] = {}
    for key, value in profile.items():
        canonical = COMPANIES_HOUSE_FIELD_MAP.get(key)
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
    """Diff a live/observed profile key list against what this adapter
    expects -- used by `python -m app.cli.companies_house_lookup` to report
    unknown/missing fields without touching normalize()/parse() logic."""
    observed = set(observed_field_names)
    known = set(COMPANIES_HOUSE_FIELD_MAP.keys()) | STRUCTURAL_FIELDS

    unknown = sorted(observed - known)
    matched_canonical = {
        COMPANIES_HOUSE_FIELD_MAP[k] for k in observed if k in COMPANIES_HOUSE_FIELD_MAP
    }
    missing_canonical = sorted(set(CANONICAL_FIELDS) - matched_canonical)
    missing_required = sorted(REQUIRED_CANONICAL_FIELDS - matched_canonical)

    return FieldComparison(
        unknown_fields=unknown,
        matched_canonical_fields=sorted(matched_canonical),
        missing_canonical_fields=missing_canonical,
        missing_required_fields=missing_required,
    )
