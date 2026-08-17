"""SEC EDGAR adapter (data.sec.gov) -- official US public-company filing
data. See docs/sec_edgar_data_access.md for what was verified live before
writing this file.

IMPORTANT SCOPE LIMITATION (per explicit task instruction, verified by
direct inspection of a live response): SEC EDGAR only covers companies that
file with the SEC -- i.e. publicly traded (or otherwise SEC-registered) US
companies. It is NOT a source of the broader US private-company universe
the way MCA is for India; most companies Super CRM would search for
(private manufacturers, distributors, service providers) will never appear
here at all. Treat this purely as an "enrichment for public companies"
source, never as US company-universe coverage. See
docs/sec_edgar_data_access.md.

Confirmed absent from the live schema (not guessed, not silently
fabricated): no reliable employee-count field, no incorporation/founding
date. Neither is normalized here.

No collection-enabled config flag beyond the standard `Source.collection_enabled`
/ `SourcePolicy` gate -- unlike FileSure/Companies House, there is no API
key to gate on (SEC EDGAR requires no authentication at all), so the
standard single DB-level gate is sufficient, matching GovernmentDatasetAdapter's
precedent. `Settings.sec_edgar_user_agent` still must be non-empty (checked
in fetch()) since SEC's fair-access policy rejects requests without a
compliant User-Agent -- see app.source_adapters.sec_edgar_client.

Like GovernmentDatasetAdapter, this adapter never writes to Company
directly -- it produces ObservationDrafts; app.ingestion.pipeline does
everything from there.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.ingestion.collectors.scrapling_collector import ScraplingCollector
from app.ingestion.normalization.address import normalize_whitespace
from app.ingestion.normalization.company_name import normalize_company_name
from app.models.enums import SourceType, VerificationType
from app.source_adapters.base import FetchResult, ObservationDraft, ParsedRecord, SourceAdapter
from app.source_adapters.sec_edgar_client import SecEdgarConfigurationError, fetch_company
from app.source_adapters.sec_edgar_field_mapping import map_submissions, select_annual_revenue

PROVIDER_NAME = "sec_edgar"

# SEC EDGAR is the official registrar/filing repository itself (not a
# reseller) -- same confidence input as a direct government feed
# (docs/confidence_engine.md).
_PROFILE_CONFIDENCE = 0.95
# Revenue is company-self-reported (via XBRL tagging) rather than
# independently verified by SEC, but it is a legally-required, audited
# figure in a 10-K -- OBSERVED rather than VERIFIED, one notch below the
# identity/address fields the registrar itself asserts.
_REVENUE_CONFIDENCE = 0.85


class SecEdgarAdapter(SourceAdapter):
    source_type = SourceType.PUBLIC_FILING.value
    collector_version = "sec-edgar-adapter/1.0.0"

    def __init__(self, source_name: str, collector: ScraplingCollector | None = None) -> None:
        self.source_name = source_name
        self._collector = collector or ScraplingCollector()

    def fetch(self, target: str) -> FetchResult:
        """`target` is a CIK (not a URL) -- any digit string up to 10
        digits; zero-padding is handled by the client."""
        settings = get_settings()
        if not settings.sec_edgar_user_agent:
            raise SecEdgarConfigurationError(
                "SEC_EDGAR_USER_AGENT is not configured -- SEC's fair-access policy rejects "
                "requests without a compliant User-Agent. See docs/sec_edgar_data_access.md."
            )

        response = fetch_company(
            target,
            user_agent=settings.sec_edgar_user_agent,
            base_url=settings.sec_edgar_base_url,
            collector=self._collector,
        )
        envelope = {
            "cik": response.cik,
            "submissions": response.submissions,
            "company_facts": response.company_facts,
            "company_facts_error": response.company_facts_error,
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
        submissions = envelope.get("submissions") or {}

        cik = str(submissions.get("cik") or envelope.get("cik") or "").strip()
        if not cik:
            return []

        mapped = map_submissions(submissions)
        mapped["cik"] = cik
        fields: dict[str, Any] = {k: ("" if v is None else str(v)) for k, v in mapped.items()}

        # Structural fields kept out of the stringified dict above and
        # handled explicitly in normalize().
        addresses = submissions.get("addresses") or {}
        fields["_address"] = addresses.get("business") or addresses.get("mailing") or {}
        fields["_recent_filings"] = (submissions.get("filings") or {}).get("recent") or {}
        fields["_company_facts"] = envelope.get("company_facts")
        fields["_company_facts_error"] = envelope.get("company_facts_error")
        fields["_retrieved_at"] = envelope.get("retrieved_at")

        return [ParsedRecord(external_ref=cik, fields=fields, source_url=fetch_result.url, source_published_at=None)]

    def validate(self, record: ParsedRecord) -> bool:
        if not super().validate(record):
            return False
        cik = record.fields.get("cik", "")
        return bool(cik) and cik.isdigit()

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
            extra_metadata: dict | None = None,
        ) -> None:
            metadata = {"provider": PROVIDER_NAME, "retrieved_at": f.get("_retrieved_at")}
            if extra_metadata:
                metadata.update(extra_metadata)
            drafts.append(
                ObservationDraft(
                    field=field,
                    raw_value=raw,
                    normalized_value=normalized,
                    confidence=confidence,
                    verification_type=verification_type,
                    metadata=metadata,
                )
            )

        if cik := f.get("cik"):
            add("cik", cik, cik.zfill(10))
            # See module docstring: SEC EDGAR is US-filed public companies
            # only. Foreign private issuers that file with the SEC would be
            # mislabeled US by this unconditional assertion -- a documented,
            # known limitation (see docs/sec_edgar_data_access.md), not
            # silently wrong; the alternative (leaving country_code unset
            # for every domestic filer, which is the overwhelming majority)
            # is a worse default for this product's country-scoped search.
            add("country_code", "US", "US")

        if name := f.get("legal_name"):
            add("legal_name", name, name.strip())
            add("canonical_name", name, normalize_company_name(name))

        # SEC provides a human-readable SIC description directly (unlike
        # Companies House, which only gives numeric codes) -- no bucketing
        # needed.
        if industry := f.get("industry"):
            add("industry", industry, industry.strip())
        if sic_code := f.get("sic_code"):
            add("sub_industry", sic_code, sic_code.strip())

        if company_type := f.get("company_type"):
            add("company_type", company_type, company_type.strip())

        if website := f.get("website"):
            add("website", website, website.strip())
        if phone := f.get("public_phone"):
            add("public_phone", phone, phone.strip())

        address = f.get("_address") or {}
        if address:
            street1 = address.get("street1")
            street2 = address.get("street2")
            city = address.get("city")
            state = address.get("stateOrCountry")
            zip_code = address.get("zipCode")

            full_address = normalize_whitespace(
                ", ".join(filter(None, [street1, street2, city, state, zip_code]))
            )
            if full_address:
                add("registered_address", full_address, full_address)
            if city:
                add("city", city, city.strip())
            if state:
                add("state", state, state.strip())
            if zip_code:
                add("postal_code", zip_code, zip_code.strip())

        # Latest annual filing metadata (form/date), Evidence-only -- there
        # is no canonical Company column for "most recent 10-K filing date".
        recent = f.get("_recent_filings") or {}
        forms = recent.get("form") or []
        filing_dates = recent.get("filingDate") or []
        accession_numbers = recent.get("accessionNumber") or []
        for i, form in enumerate(forms):
            if form == "10-K" and i < len(filing_dates):
                add(
                    "latest_annual_filing",
                    filing_dates[i],
                    filing_dates[i],
                    verification_type=VerificationType.OBSERVED.value,
                    extra_metadata={
                        "form": form,
                        "accession_number": accession_numbers[i] if i < len(accession_numbers) else None,
                    },
                )
                break

        drafts.extend(_normalize_revenue(f.get("_company_facts")))

        return drafts


def _normalize_revenue(company_facts: dict | None) -> list[ObservationDraft]:
    """Revenue is recorded as an Evidence-only field (`annual_revenue_usd`),
    deliberately NOT projected onto `Company.annual_revenue_inr` --
    that column is INR-specific, and writing a USD figure into it would be
    a real currency-mislabeling bug, not a rounding/scope shortcut. Full
    provenance (fiscal year, form, XBRL concept used, accession number) is
    preserved via ObservationDraft.metadata regardless. Surfacing USD
    revenue in the flat revenue_inr search filter would require the
    currency-generic revenue_amount/revenue_currency schema change flagged
    in this project's multi-source architecture audit -- out of scope for
    this adapter. See docs/sec_edgar_data_access.md."""
    if not company_facts:
        return []

    revenue = select_annual_revenue(company_facts)
    if revenue is None:
        return []

    return [
        ObservationDraft(
            field="annual_revenue_usd",
            raw_value=str(revenue.value_usd),
            normalized_value=str(revenue.value_usd),
            confidence=_REVENUE_CONFIDENCE,
            verification_type=VerificationType.OBSERVED.value,
            metadata={
                "provider": PROVIDER_NAME,
                "currency": "USD",
                "fiscal_year": revenue.fiscal_year,
                "xbrl_concept": revenue.concept,
                "form": revenue.form,
                "accession_number": revenue.accession_number,
            },
        )
    ]
