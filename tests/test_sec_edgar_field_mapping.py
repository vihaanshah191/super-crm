"""Tests for app/source_adapters/sec_edgar_field_mapping.py. Fixture shapes
mirror the real, live-verified companyfacts.json/submissions.json structure
(see docs/sec_edgar_data_access.md) with synthetic values -- not a real
filer's data."""

from app.source_adapters.sec_edgar_field_mapping import (
    compare_fields,
    map_submissions,
    select_annual_revenue,
)


class TestMapSubmissions:
    def test_maps_known_fields(self):
        submissions = {
            "cik": "0000320193",
            "name": "Test Public Co",
            "sicDescription": "Electronic Computers",
            "sic": "3571",
            "entityType": "operating",
        }
        mapped = map_submissions(submissions)
        assert mapped["legal_name"] == "Test Public Co"
        assert mapped["industry"] == "Electronic Computers"
        assert mapped["sic_code"] == "3571"
        assert mapped["company_type"] == "operating"

    def test_drops_null_and_empty_values(self):
        submissions = {"name": "Test Public Co", "website": None, "phone": ""}
        mapped = map_submissions(submissions)
        assert "website" not in mapped
        assert "phone" not in mapped

    def test_drops_unknown_fields_without_crashing(self):
        submissions = {"name": "Test Public Co", "someFutureField": "xyz"}
        mapped = map_submissions(submissions)
        assert "someFutureField" not in mapped


class TestSelectAnnualRevenue:
    def test_prefers_modern_concept_over_legacy(self):
        company_facts = {
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {
                            "USD": [
                                {"start": "2023-01-01", "end": "2023-12-31", "val": 100_000_000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01", "accn": "0001-24-000001"},
                            ]
                        }
                    },
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"start": "2023-01-01", "end": "2023-12-31", "val": 999_000_000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01", "accn": "0001-24-000001"},
                            ]
                        }
                    },
                }
            }
        }
        revenue = select_annual_revenue(company_facts)
        assert revenue is not None
        assert revenue.value_usd == 100_000_000
        assert revenue.concept == "RevenueFromContractWithCustomerExcludingAssessedTax"

    def test_falls_back_to_legacy_concept_when_modern_absent(self):
        company_facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"start": "2016-01-01", "end": "2016-12-31", "val": 50_000_000, "fy": 2016, "fp": "FY", "form": "10-K", "filed": "2017-02-01", "accn": "x"},
                            ]
                        }
                    }
                }
            }
        }
        revenue = select_annual_revenue(company_facts)
        assert revenue is not None
        assert revenue.value_usd == 50_000_000
        assert revenue.concept == "Revenues"

    def test_ignores_quarterly_entries_only_takes_full_year(self):
        company_facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"start": "2023-01-01", "end": "2023-03-31", "val": 10_000_000, "fy": 2023, "fp": "Q1", "form": "10-Q", "filed": "2023-05-01", "accn": "x"},
                                {"start": "2023-01-01", "end": "2023-12-31", "val": 40_000_000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01", "accn": "y"},
                            ]
                        }
                    }
                }
            }
        }
        revenue = select_annual_revenue(company_facts)
        assert revenue is not None
        assert revenue.value_usd == 40_000_000

    def test_picks_most_recent_fiscal_year_when_multiple_present(self):
        company_facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"start": "2021-01-01", "end": "2021-12-31", "val": 10_000_000, "fy": 2021, "fp": "FY", "form": "10-K", "filed": "2022-02-01", "accn": "x"},
                                {"start": "2022-01-01", "end": "2022-12-31", "val": 20_000_000, "fy": 2022, "fp": "FY", "form": "10-K", "filed": "2023-02-01", "accn": "y"},
                            ]
                        }
                    }
                }
            }
        }
        revenue = select_annual_revenue(company_facts)
        assert revenue is not None
        assert revenue.value_usd == 20_000_000
        assert revenue.fiscal_year == 2022

    def test_no_revenue_concepts_present_returns_none(self):
        assert select_annual_revenue({"facts": {"us-gaap": {}}}) is None

    def test_missing_company_facts_returns_none(self):
        assert select_annual_revenue(None) is None

    def test_non_usd_only_data_returns_none(self):
        """A concept present only in a non-USD unit (e.g. a foreign filer
        reporting in EUR) must not be silently treated as USD."""
        company_facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "EUR": [
                                {"start": "2023-01-01", "end": "2023-12-31", "val": 40_000_000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01", "accn": "x"},
                            ]
                        }
                    }
                }
            }
        }
        assert select_annual_revenue(company_facts) is None


class TestCompareFields:
    def test_reports_matched_and_missing_fields(self):
        comparison = compare_fields(["name", "sicDescription", "someUnknownField"])
        assert "legal_name" in comparison.matched_canonical_fields
        assert "someUnknownField" in comparison.unknown_fields

    def test_missing_required_field_is_reported(self):
        comparison = compare_fields(["name"])
        assert "cik" in comparison.missing_required_fields
