"""UK Companies House adapter (api.company-information.service.gov.uk) --
the official UK company registry, equivalent in role to
GovernmentDatasetAdapter/MCA for India. See docs/companies_house_data_access.md
for what was verified live before writing this file.

Two independent, layered gates control whether this adapter's fetch() can
ever run:
  1. `Settings.companies_house_collection_enabled` -- a hard config-level
     switch, checked directly in fetch(), independent of any database state.
  2. The standard `Source.collection_enabled` / `SourcePolicy` compliance
     gate (see app/compliance/source_policy.py), checked by
     app.ingestion.pipeline.ingest_parsed_record() before fetch() is ever
     reached in the normal ingestion path.
Both must be true. This mirrors FileSureAdapter and "collection is blocked
in two independent places, not just one" from docs/compliance.md -- applied
here for architectural consistency and an independent kill-switch, even
though Companies House itself is free (unlike FileSure).

Known limitation, confirmed by reading the documented company-profile
response shape: this endpoint does NOT include filed financial figures
(revenue/turnover) -- only filing *metadata* (accounts.next_due,
confirmation_statement.next_due, has_charges, has_insolvency_history),
which is preserved here as Evidence-only fields, not projected onto any
Company revenue column. See docs/companies_house_data_access.md.

Like GovernmentDatasetAdapter, this adapter never writes to Company
directly -- it produces ObservationDrafts; app.ingestion.pipeline does
everything from there (entity resolution, confidence-weighted Evidence,
canonical Company projection).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.ingestion.collectors.scrapling_collector import ScraplingCollector
from app.ingestion.normalization.address import normalize_whitespace
from app.ingestion.normalization.company_name import normalize_company_name
from app.ingestion.normalization.dates import parse_flexible_date
from app.models.enums import SourceType, VerificationType
from app.source_adapters.base import FetchResult, ObservationDraft, ParsedRecord, SourceAdapter
from app.source_adapters.companies_house_client import CompaniesHouseConfigurationError, fetch_company
from app.source_adapters.companies_house_field_mapping import map_company_profile, sic_code_to_section

PROVIDER_NAME = "companies_house"

# Companies House IS the registrar itself (not a reseller, unlike FileSure)
# -- same confidence input as a direct MCA feed (docs/confidence_engine.md).
_PROFILE_CONFIDENCE = 0.95


class CompaniesHouseAdapter(SourceAdapter):
    source_type = SourceType.GOVERNMENT_DATASET.value
    collector_version = "companies-house-adapter/1.0.0"

    def __init__(self, source_name: str, collector: ScraplingCollector | None = None) -> None:
        self.source_name = source_name
        self._collector = collector or ScraplingCollector()

    def fetch(self, target: str) -> FetchResult:
        """`target` is a UK company number (not a URL)."""
        settings = get_settings()
        if not settings.companies_house_collection_enabled:
            raise CompaniesHouseConfigurationError(
                "COMPANIES_HOUSE_COLLECTION_ENABLED is false -- Companies House collection is "
                "disabled regardless of any other configuration. See docs/companies_house_data_access.md."
            )
        if not settings.companies_house_api_key:
            raise CompaniesHouseConfigurationError("COMPANIES_HOUSE_API_KEY is not configured.")

        response = fetch_company(
            target,
            api_key=settings.companies_house_api_key,
            base_url=settings.companies_house_base_url,
            collector=self._collector,
        )
        envelope = {
            "company_number": response.company_number,
            "profile": response.profile,
            "retrieved_at": response.retrieved_at_iso,
        }
        return FetchResult(
            url=response.source_url,
            status_code=200,
            content=json.dumps(envelope).encode("utf-8"),
            content_type="application/json",
            fetched_at=datetime.now(timezone.utc),
            metadata={"provider": PROVIDER_NAME},
        )

    def parse(self, fetch_result: FetchResult) -> list[ParsedRecord]:
        envelope = json.loads(fetch_result.content.decode("utf-8"))
        profile = envelope.get("profile") or {}

        company_number = str(profile.get("company_number") or envelope.get("company_number") or "").strip().upper()
        if not company_number:
            return []

        mapped = map_company_profile(profile)
        fields: dict[str, Any] = {k: ("" if v is None else str(v)) for k, v in mapped.items()}

        # Structural fields kept out of the stringified dict above and
        # handled explicitly in normalize().
        fields["_sic_codes"] = profile.get("sic_codes") or []
        fields["_registered_office_address"] = profile.get("registered_office_address") or {}
        fields["_accounts"] = profile.get("accounts") or {}
        fields["_confirmation_statement"] = profile.get("confirmation_statement") or {}
        fields["_has_charges"] = profile.get("has_charges")
        fields["_has_insolvency_history"] = profile.get("has_insolvency_history")
        fields["_retrieved_at"] = envelope.get("retrieved_at")

        return [
            ParsedRecord(
                external_ref=company_number, fields=fields, source_url=fetch_result.url, source_published_at=None
            )
        ]

    def validate(self, record: ParsedRecord) -> bool:
        if not super().validate(record):
            return False
        company_number = record.fields.get("company_number", "")
        return len(company_number) == 8

    def normalize(self, record: ParsedRecord) -> list[ObservationDraft]:
        drafts: list[ObservationDraft] = []
        f = record.fields

        def add(
            field: str,
            raw: str,
            normalized: str | None,
            *,
            confidence: float = _PROFILE_CONFIDENCE,
            verification_type: str = VerificationType.VERIFIED.value,
        ) -> None:
            drafts.append(
                ObservationDraft(
                    field=field,
                    raw_value=raw,
                    normalized_value=normalized,
                    confidence=confidence,
                    verification_type=verification_type,
                    metadata={"provider": PROVIDER_NAME, "retrieved_at": f.get("_retrieved_at")},
                )
            )

        if company_number := f.get("company_number"):
            add("company_number", company_number, company_number.upper())
            # Companies House is exclusively UK-registered companies --
            # safe to assert unconditionally, same pattern as MCA/FileSure
            # asserting country_code="IN". Populates Company.country_code so
            # country_scope-restricted saved searches don't silently
            # exclude real UK companies.
            add("country_code", "GB", "GB")

        if name := f.get("legal_name"):
            add("legal_name", name, name.strip())
            add("canonical_name", name, normalize_company_name(name))

        if status := f.get("company_status"):
            add("company_status", status, status.strip().lower())

        if company_type := f.get("company_type"):
            add("company_type", company_type, company_type.strip())

        if jurisdiction := f.get("jurisdiction"):
            add("jurisdiction", jurisdiction, jurisdiction.strip())

        if inc_date := f.get("incorporation_date"):
            parsed_date = parse_flexible_date(inc_date)
            if parsed_date:
                add("incorporation_date", inc_date, parsed_date.isoformat())

        sic_codes = f.get("_sic_codes") or []
        if sic_codes:
            raw_codes = ", ".join(sic_codes)
            add("sub_industry", raw_codes, raw_codes)
            # First SIC code's section is treated as the company's primary
            # industry bucket -- Companies House lists sic_codes in the
            # order the filer supplied them, most-significant first, per
            # its own guidance.
            section = sic_code_to_section(sic_codes[0])
            if section:
                add("industry", sic_codes[0], section)

        address = f.get("_registered_office_address") or {}
        if address:
            line1 = address.get("address_line_1")
            line2 = address.get("address_line_2")
            locality = address.get("locality")
            region = address.get("region")
            postal_code = address.get("postal_code")
            country = address.get("country")

            full_address = normalize_whitespace(
                ", ".join(filter(None, [line1, line2, locality, region, postal_code, country]))
            )
            if full_address:
                add("registered_address", full_address, full_address)
            if locality:
                add("city", locality, locality.strip())
            if region:
                add("state", region, region.strip())
            if postal_code:
                add("postal_code", postal_code, postal_code.strip())

        # Filing metadata -- confirmed present in the company-profile
        # response, but these are due-dates/flags, not filed financial
        # figures. Evidence-only: no canonical Company column exists (or
        # should exist) for "when is the next confirmation statement due".
        accounts = f.get("_accounts") or {}
        if next_due := accounts.get("next_due"):
            add("accounts_next_due", next_due, next_due, verification_type=VerificationType.OBSERVED.value)
        confirmation = f.get("_confirmation_statement") or {}
        if next_due := confirmation.get("next_due"):
            add(
                "confirmation_statement_next_due",
                next_due,
                next_due,
                verification_type=VerificationType.OBSERVED.value,
            )
        if (has_charges := f.get("_has_charges")) is not None:
            add("has_charges", str(has_charges), str(has_charges).lower())
        if (has_insolvency := f.get("_has_insolvency_history")) is not None:
            add(
                "has_insolvency_history",
                str(has_insolvency),
                str(has_insolvency).lower(),
            )

        return drafts
