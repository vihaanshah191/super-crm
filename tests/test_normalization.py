import pytest

from app.ingestion.normalization.address import extract_postal_code, normalize_domain, normalize_whitespace
from app.ingestion.normalization.company_name import normalize_company_name, strip_legal_suffix
from app.ingestion.normalization.employee_range import parse_employee_range
from app.ingestion.normalization.revenue import CRORE, LAKH, parse_inr_revenue


class TestCompanyNameNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("ABC Industries Pvt. Ltd.", "abc industries"),
            ("ABC INDUSTRIES PRIVATE LIMITED", "abc industries"),
            ("ABC Industries", "abc industries"),
            ("Sunrise Metals & Alloys LLP", "sunrise metals alloys"),
            ("  Extra   Space   Corp  ", "extra space"),
        ],
    )
    def test_normalize_company_name(self, raw, expected):
        assert normalize_company_name(raw) == expected

    def test_different_legal_suffixes_converge(self):
        variants = [
            "ABC Industries",
            "ABC Industries Pvt Ltd",
            "ABC Industries Private Limited",
            "ABC INDUSTRIES PVT. LTD.",
        ]
        normalized = {normalize_company_name(v) for v in variants}
        assert normalized == {"abc industries"}

    def test_strip_legal_suffix_preserves_name_without_suffix(self):
        assert strip_legal_suffix("ABC Industries") == "ABC Industries"

    def test_empty_name(self):
        assert normalize_company_name("") == ""


class TestRevenueNormalization:
    def test_crore_value(self):
        result = parse_inr_revenue("₹10 crore")
        assert result.value_inr == 10 * CRORE

    def test_lakh_value(self):
        result = parse_inr_revenue("Rs. 1.2 lakh")
        assert result.value_inr == pytest.approx(1.2 * LAKH)

    def test_range_with_shared_unit(self):
        result = parse_inr_revenue("10-50 crore")
        assert result.range_min_inr == 10 * CRORE
        assert result.range_max_inr == 50 * CRORE

    def test_range_with_to_keyword(self):
        result = parse_inr_revenue("10 to 50 crore")
        assert result.range_min_inr == 10 * CRORE
        assert result.range_max_inr == 50 * CRORE

    def test_plain_numeric(self):
        result = parse_inr_revenue("128000000")
        assert result.value_inr == 128_000_000

    def test_unparseable_text_returns_none(self):
        assert parse_inr_revenue("revenue not disclosed") is None

    def test_empty_text_returns_none(self):
        assert parse_inr_revenue("") is None

    def test_never_stores_crore_as_primary_value(self):
        # Sanity check on the product requirement: the parsed value must be a
        # plain INR number, not "10 crore" left as a string.
        result = parse_inr_revenue("₹10 crore")
        assert result.value_inr == 100_000_000
        assert isinstance(result.value_inr, float)


class TestEmployeeRangeNormalization:
    def test_plain_count(self):
        result = parse_employee_range("34")
        assert result.count == 34
        assert result.range_min == 34
        assert result.range_max == 34

    def test_range(self):
        result = parse_employee_range("50-200 employees")
        assert result.range_min == 50
        assert result.range_max == 200
        assert result.count is None

    def test_plus_notation(self):
        result = parse_employee_range("1000+ employees")
        assert result.range_min == 1000
        assert result.range_max is None

    def test_unparseable_returns_none(self):
        assert parse_employee_range("not disclosed") is None


class TestAddressNormalization:
    def test_normalize_domain_strips_scheme_and_www(self):
        assert normalize_domain("https://www.Example.com/about") == "example.com"

    def test_normalize_domain_bare_domain(self):
        assert normalize_domain("www.abcindustries.example") == "abcindustries.example"

    def test_normalize_domain_empty(self):
        assert normalize_domain("") is None

    def test_extract_postal_code(self):
        assert extract_postal_code("Plot 45, MIDC, Pune, Maharashtra 411019, India") == "411019"

    def test_extract_postal_code_missing(self):
        assert extract_postal_code("Plot 45, MIDC, Pune") is None

    def test_normalize_whitespace(self):
        assert normalize_whitespace("  a   b\n c ") == "a b c"
