"""Tests for the pure/offline parts of app/cli/inspect_mca_schema.py. The
actual live-API call is deliberately not covered here -- it requires a real
DATA_GOV_IN_API_KEY and network access; see docs/mca_data_access.md. What's
tested: URL construction and the no-key clean-failure path (both DB-free and
network-free)."""

from app.cli.inspect_mca_schema import _build_url, _python_type_name, run
from app.core.config import Settings


class TestBuildUrl:
    def test_uses_full_resource_url_override_when_set(self):
        settings = Settings(
            data_gov_in_api_key="testkey",
            data_gov_in_mca_resource_url="https://example.test/custom-resource",
        )
        url = _build_url(settings, limit=5)
        assert url.startswith("https://example.test/custom-resource?")
        assert "api-key=testkey" in url
        assert "limit=5" in url

    def test_constructs_from_resource_id_when_no_url_override(self):
        settings = Settings(data_gov_in_api_key="testkey", data_gov_in_mca_resource_url="")
        url = _build_url(settings, limit=3)
        assert url.startswith("https://api.data.gov.in/resource/")
        assert settings.data_gov_in_mca_resource_id in url
        assert "format=json" in url
        assert "limit=3" in url


class TestPythonTypeName:
    def test_none_is_reported_as_null_not_nonetype(self):
        assert _python_type_name(None) == "null"

    def test_reports_actual_python_type_name(self):
        assert _python_type_name("x") == "str"
        assert _python_type_name(5) == "int"
        assert _python_type_name(5.0) == "float"


class TestRunWithoutApiKey:
    def test_missing_api_key_exits_1_without_network_or_db(self, monkeypatch, capsys):
        monkeypatch.setattr("app.cli.inspect_mca_schema.get_settings", lambda: Settings(data_gov_in_api_key=""))
        exit_code = run(limit=5)
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "DATA_GOV_IN_API_KEY is not set" in captured.err
