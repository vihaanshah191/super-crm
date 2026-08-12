"""Tests for the generic source metadata added in Migration 1
(sources.display_name/countries/access_method/compliance_status) --
see docs/multi_source_architecture.md Section G."""

from sqlalchemy import select

from app.models.enums import SourceAccessMethod, SourceComplianceStatus
from app.models.source import Source


class TestSourceModelDefaults:
    def test_new_source_defaults_to_unreviewed_and_unclassified(self, db):
        source = Source(name="defaults_test_source", source_type="website", reliability_weight=40)
        db.add(source)
        db.commit()
        db.refresh(source)

        assert source.display_name is None
        assert source.countries == []
        assert source.access_method == SourceAccessMethod.UNKNOWN.value
        assert source.compliance_status == SourceComplianceStatus.UNDER_REVIEW.value

    def test_explicit_values_are_stored(self, db):
        source = Source(
            name="explicit_test_source",
            display_name="Explicit Test Source",
            source_type="registry_data_provider",
            countries=["IN", "US"],
            access_method=SourceAccessMethod.OFFICIAL_API.value,
            compliance_status=SourceComplianceStatus.ACTIVE.value,
            reliability_weight=80,
        )
        db.add(source)
        db.commit()
        db.refresh(source)

        assert source.display_name == "Explicit Test Source"
        assert source.countries == ["IN", "US"]
        assert source.access_method == "official_api"
        assert source.compliance_status == "active"


class TestCliSourceBootstrapsSetComplianceStatus:
    """None of Google/LinkedIn/Facebook/Justdial have adapters in this
    codebase (see docs/multi_source_architecture.md Section I) -- these
    tests instead confirm the sources that DO exist declare an honest
    status rather than defaulting silently."""

    def test_filesure_source_is_active_official_api(self, db):
        from app.cli.filesure_lookup import _get_or_create_source

        source = _get_or_create_source(db)
        assert source.access_method == "official_api"
        assert source.compliance_status == "active"
        assert source.countries == ["IN"]

    def test_custom_file_source_is_active_user_uploaded(self, db):
        from app.cli.import_custom_source import _get_or_create_source

        source = _get_or_create_source(
            db, "metadata_test_custom", field_mapping={"Name": "legal_name"}, declared_origin="test"
        )
        assert source.access_method == "user_uploaded_file"
        assert source.compliance_status == "active"

    def test_mca_file_import_source_is_active(self, db):
        from app.cli.import_mca import _get_or_create_file_import_source

        source = _get_or_create_file_import_source(db, license_text="Government Open Data License - India (GODL)")
        assert source.access_method == "user_uploaded_file"
        assert source.compliance_status == "active"
        assert source.countries == ["IN"]

    def test_dev_seed_source_is_not_available(self, db):
        from app.cli.seed_dev import _get_or_create_dev_source

        source = _get_or_create_dev_source(db)
        assert source.compliance_status == "not_available"
