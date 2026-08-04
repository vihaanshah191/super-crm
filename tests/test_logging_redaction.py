from app.core.logging import scrub_secrets


class TestScrubSecrets:
    def test_redacts_api_key_query_param(self):
        url = "https://api.data.gov.in/resource/abc?api-key=SECRET123&format=json"
        assert "SECRET123" not in scrub_secrets(url)
        assert "api-key=***" in scrub_secrets(url)

    def test_redacts_api_key_with_underscore_variant(self):
        assert "SECRET" not in scrub_secrets("https://example.test?api_key=SECRET")

    def test_redacts_token_and_secret_params(self):
        assert "TOK" not in scrub_secrets("https://example.test?token=TOK")
        assert "SEC" not in scrub_secrets("https://example.test?secret=SEC")

    def test_leaves_unrelated_text_unchanged(self):
        text = "fetch_static failed for 'https://example.test/data.csv' after 2 attempt(s)"
        assert scrub_secrets(text) == text

    def test_case_insensitive_match(self):
        assert "SECRET" not in scrub_secrets("https://example.test?API-KEY=SECRET")
