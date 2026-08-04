from app.source_adapters.mca_field_mapping import (
    CANONICAL_FIELDS,
    MCA_EXTERNAL_FIELD_MAP,
    compare_fields,
    map_external_fields,
)


class TestMapExternalFields:
    def test_standard_column_names_map_to_canonical_keys(self):
        row = {"CIN": "U24100MH2015PTC123456", "COMPANY_NAME": "ABC Industries", "REGISTERED_STATE": "Maharashtra"}
        mapped = map_external_fields(row)
        assert mapped == {"cin": "U24100MH2015PTC123456", "company_name": "ABC Industries", "registered_state": "Maharashtra"}

    def test_unknown_columns_are_silently_dropped_not_an_error(self):
        row = {"CIN": "U24100MH2015PTC123456", "SOME_BRAND_NEW_COLUMN": "unexpected"}
        mapped = map_external_fields(row)
        assert mapped == {"cin": "U24100MH2015PTC123456"}

    def test_efiling_suffix_is_stripped_before_lookup(self):
        row = {"COMPANY_STATUS(for efiling)": "Active"}
        assert map_external_fields(row) == {"company_status": "Active"}

    def test_authorized_cap_alias_maps_to_authorized_capital(self):
        row = {"AUTHORIZED_CAP": "1000000"}
        assert map_external_fields(row) == {"authorized_capital": "1000000"}

    def test_multiple_aliases_map_to_same_canonical_field(self):
        assert map_external_fields({"ROC": "RoC-Pune"}) == {"roc": "RoC-Pune"}
        assert map_external_fields({"REGISTRAR_OF_COMPANIES": "RoC-Pune"}) == {"roc": "RoC-Pune"}
        assert map_external_fields({"ROC_CODE": "RoC-Pune"}) == {"roc": "RoC-Pune"}


class TestCompareFields:
    def test_all_known_columns_yields_no_unknown_or_missing_required(self):
        comparison = compare_fields(list(MCA_EXTERNAL_FIELD_MAP.keys()))
        assert comparison.unknown_external_fields == []
        assert comparison.missing_required_fields == []

    def test_unrecognized_column_is_reported_as_unknown(self):
        comparison = compare_fields(["CIN", "COMPANY_NAME", "TOTALLY_NEW_FIELD"])
        assert "TOTALLY_NEW_FIELD" in comparison.unknown_external_fields

    def test_missing_cin_column_is_reported_as_required_and_missing(self):
        comparison = compare_fields(["COMPANY_NAME", "REGISTERED_STATE"])
        assert "cin" in comparison.missing_required_fields
        assert "cin" in comparison.missing_canonical_fields

    def test_matched_canonical_fields_reflects_what_was_found(self):
        comparison = compare_fields(["CIN", "COMPANY_NAME"])
        assert set(comparison.matched_canonical_fields) == {"cin", "company_name"}
        assert set(CANONICAL_FIELDS) - {"cin", "company_name"} <= set(comparison.missing_canonical_fields)
