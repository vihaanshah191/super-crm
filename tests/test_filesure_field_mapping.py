from app.source_adapters.filesure_field_mapping import (
    CANONICAL_FIELDS,
    FILESURE_COMPANY_FIELD_MAP,
    compare_fields,
    map_company_fields,
)


class TestMapCompanyFields:
    def test_known_fields_map_to_canonical_keys(self):
        company_data = {"cin": "L74110KA2013PLC096530", "companyName": "SWIGGY LIMITED", "companyStatus": "Active"}
        mapped = map_company_fields(company_data)
        assert mapped == {"cin": "L74110KA2013PLC096530", "company_name": "SWIGGY LIMITED", "company_status": "Active"}

    def test_unknown_fields_are_silently_dropped(self):
        company_data = {"cin": "X", "someBrandNewField": "unexpected"}
        assert map_company_fields(company_data) == {"cin": "X"}

    def test_structural_address_field_is_not_flattened_here(self):
        """MCAMDSCompanyAddress is handled structurally in the adapter, not
        via this flat rename map."""
        company_data = {"MCAMDSCompanyAddress": [{"city": "Bengaluru"}]}
        assert map_company_fields(company_data) == {}


class TestCompareFields:
    def test_all_known_fields_yields_no_unknown_or_missing_required(self):
        comparison = compare_fields(list(FILESURE_COMPANY_FIELD_MAP.keys()) + ["MCAMDSCompanyAddress"])
        assert comparison.unknown_fields == []
        assert comparison.missing_required_fields == []

    def test_unrecognized_field_is_reported_as_unknown(self):
        comparison = compare_fields(["cin", "brandNewFutureField"])
        assert "brandNewFutureField" in comparison.unknown_fields

    def test_missing_cin_is_reported_as_required_and_missing(self):
        comparison = compare_fields(["companyName", "companyStatus"])
        assert "cin" in comparison.missing_required_fields

    def test_matched_canonical_fields_reflects_input(self):
        comparison = compare_fields(["cin", "companyName"])
        assert set(comparison.matched_canonical_fields) == {"cin", "company_name"}
        assert set(CANONICAL_FIELDS) - {"cin", "company_name"} <= set(comparison.missing_canonical_fields)
