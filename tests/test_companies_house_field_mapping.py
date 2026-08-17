"""Tests for app/source_adapters/companies_house_field_mapping.py."""

from app.source_adapters.companies_house_field_mapping import (
    compare_fields,
    map_company_profile,
    sic_code_to_section,
)


class TestSicCodeToSection:
    def test_manufacturing_range(self):
        assert sic_code_to_section("25620") == "Manufacturing"
        assert sic_code_to_section("10000") == "Manufacturing"
        assert sic_code_to_section("33200") == "Manufacturing"

    def test_information_and_communication_range(self):
        assert sic_code_to_section("62012") == "Information and Communication"

    def test_boundary_just_outside_manufacturing_is_construction(self):
        assert sic_code_to_section("41000") == "Construction"

    def test_single_division_section(self):
        assert sic_code_to_section("68100") == "Real Estate Activities"

    def test_unrecognized_division_returns_none(self):
        assert sic_code_to_section("00100") is None

    def test_malformed_code_returns_none(self):
        assert sic_code_to_section("") is None
        assert sic_code_to_section("X") is None


class TestMapCompanyProfile:
    def test_maps_known_fields(self):
        profile = {"company_name": "Test Ltd", "company_number": "00000006", "company_status": "active"}
        mapped = map_company_profile(profile)
        assert mapped == {
            "legal_name": "Test Ltd",
            "company_number": "00000006",
            "company_status": "active",
        }

    def test_drops_unknown_fields_without_crashing(self):
        profile = {"company_name": "Test Ltd", "some_future_field": "xyz"}
        mapped = map_company_profile(profile)
        assert "some_future_field" not in mapped
        assert mapped["legal_name"] == "Test Ltd"


class TestCompareFields:
    def test_reports_matched_and_missing_fields(self):
        comparison = compare_fields(["company_name", "company_number", "an_unknown_field"])
        assert "legal_name" in comparison.matched_canonical_fields
        assert "an_unknown_field" in comparison.unknown_fields
        assert comparison.missing_required_fields == []  # company_number was observed

    def test_missing_required_field_is_reported(self):
        comparison = compare_fields(["company_name"])
        assert "company_number" in comparison.missing_required_fields
