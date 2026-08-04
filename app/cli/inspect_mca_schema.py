"""Read-only MCA live-schema discovery.

    python -m app.cli.inspect_mca_schema [--limit N]

Requires DATA_GOV_IN_API_KEY. Fetches a very small sample (default 5 rows)
from the official data.gov.in REST API for the MCA Company Master Data
resource, then prints:

  - resource metadata (title, org, sector, source, update dates, declared
    field list, total record count -- whatever the API response includes)
  - the actual field/column names present in the sample records
  - a representative Python type for each field's value
  - a diff against app/source_adapters/mca_field_mapping.py: which observed
    columns we don't recognize, and which fields we expect but didn't see

This command deliberately does NOT import app.db.base or any app.models --
it cannot write to the database even by accident. It exists so the adapter's
field mapping can be verified (or corrected) against the real dataset the
moment API access exists, instead of continuing to run against untested
assumptions. Until it has actually been run successfully once, nothing in
this codebase should be described as having a "verified live schema" -- see
docs/mca_data_access.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import urlencode

from app.core.config import get_settings
from app.core.logging import scrub_secrets
from app.ingestion.collectors.scrapling_collector import FetchError, ScraplingCollector
from app.source_adapters.mca_field_mapping import compare_fields

API_BASE = "https://api.data.gov.in/resource"


def _build_url(settings, limit: int) -> str:
    if settings.data_gov_in_mca_resource_url:
        base = settings.data_gov_in_mca_resource_url
        joiner = "&" if "?" in base else "?"
        return f"{base}{joiner}{urlencode({'api-key': settings.data_gov_in_api_key, 'format': 'json', 'limit': limit})}"
    resource_id = settings.data_gov_in_mca_resource_id
    query = urlencode({"api-key": settings.data_gov_in_api_key, "format": "json", "limit": limit})
    return f"{API_BASE}/{resource_id}?{query}"


def _python_type_name(value: object) -> str:
    if value is None:
        return "null"
    return type(value).__name__


def run(limit: int) -> int:
    settings = get_settings()

    if not settings.data_gov_in_api_key:
        print(
            "DATA_GOV_IN_API_KEY is not set. This command requires a real "
            "data.gov.in API key to query the live MCA dataset -- register "
            "for a free one at https://www.data.gov.in (Profile -> API key) "
            "and set DATA_GOV_IN_API_KEY, then re-run this command.\n\n"
            "MCA collection stays disabled (Source.collection_enabled=False) "
            "regardless of this key -- that is a separate, deliberate switch. "
            "See docs/mca_data_access.md.",
            file=sys.stderr,
        )
        return 1

    url = _build_url(settings, limit)
    masked_url = url.replace(settings.data_gov_in_api_key, "***")
    print(f"Requesting: {masked_url}\n")

    collector = ScraplingCollector()
    try:
        fetch_result = collector.fetch_static(url, max_retries=1)
    except FetchError as exc:
        print(f"Request failed: {scrub_secrets(str(exc))}", file=sys.stderr)
        return 1

    if fetch_result.status_code != 200:
        print(f"Unexpected HTTP status {fetch_result.status_code}:", file=sys.stderr)
        print(scrub_secrets(fetch_result.content.decode("utf-8", errors="replace"))[:2000], file=sys.stderr)
        return 1

    try:
        payload = json.loads(fetch_result.content.decode("utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Response was not valid JSON: {exc}", file=sys.stderr)
        print(fetch_result.content.decode("utf-8", errors="replace")[:2000], file=sys.stderr)
        return 1

    print("=" * 70)
    print("RESOURCE METADATA")
    print("=" * 70)
    records = payload.pop("records", None) if isinstance(payload, dict) else None
    for key, value in (payload.items() if isinstance(payload, dict) else []):
        print(f"  {key}: {value}")

    if not records:
        print("\nNo 'records' array in the response -- nothing further to inspect.")
        print("Full response (truncated):")
        print(json.dumps(payload, indent=2)[:2000])
        return 0

    print()
    print("=" * 70)
    print(f"OBSERVED FIELDS (from {len(records)} sample record(s))")
    print("=" * 70)

    all_field_names: set[str] = set()
    for record in records:
        if isinstance(record, dict):
            all_field_names.update(record.keys())

    for field_name in sorted(all_field_names):
        observed_types = sorted({_python_type_name(r.get(field_name)) for r in records if isinstance(r, dict)})
        sample_value = next((r.get(field_name) for r in records if isinstance(r, dict) and r.get(field_name)), None)
        print(f"  {field_name!r}: type(s)={observed_types} sample={sample_value!r}")

    print()
    print("=" * 70)
    print("COMPARISON AGAINST app/source_adapters/mca_field_mapping.py")
    print("=" * 70)
    comparison = compare_fields(sorted(all_field_names))

    print(f"\nMatched canonical fields ({len(comparison.matched_canonical_fields)}):")
    for name in comparison.matched_canonical_fields:
        print(f"  - {name}")

    print(f"\nUnknown external fields -- present in the live data, not in our mapping ({len(comparison.unknown_external_fields)}):")
    if not comparison.unknown_external_fields:
        print("  (none)")
    for name in comparison.unknown_external_fields:
        print(f"  - {name}  <-- consider adding to MCA_EXTERNAL_FIELD_MAP")

    print(f"\nExpected-but-missing canonical fields -- we know how to use these, none of the live columns map to them ({len(comparison.missing_canonical_fields)}):")
    if not comparison.missing_canonical_fields:
        print("  (none)")
    for name in comparison.missing_canonical_fields:
        print(f"  - {name}")

    if comparison.missing_required_fields:
        print(f"\n*** REQUIRED fields missing: {comparison.missing_required_fields} ***")
        print("Rows from this dataset would fail validate() entirely until this is fixed.")

    print()
    print("This command did not write anything to the database.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=5, help="Number of sample rows to request (default: 5)")
    args = parser.parse_args()
    sys.exit(run(args.limit))


if __name__ == "__main__":
    main()
