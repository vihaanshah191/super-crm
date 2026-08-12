from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://super_crm:super_crm@localhost:5432/super_crm"

    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"

    scrapling_default_timeout_seconds: int = 30
    scrapling_max_response_bytes: int = 10 * 1024 * 1024
    ingestion_default_concurrency: int = 4
    # Browser TLS-fingerprint to impersonate via curl_cffi (anti-bot-detection
    # behavior -- "chrome" is Scrapling's own default and should stay the
    # default here too). Empty string disables impersonation. Exists as a
    # config knob, not a hardcoded default change, because some
    # TLS-intercepting outbound proxies (observed in this project's own
    # sandboxed dev environment -- see docs/filesure_data_access.md) reset
    # the connection when curl_cffi's impersonated ClientHello reaches them;
    # disabling impersonation is a local/dev workaround, never appropriate
    # to ship as the default since it weakens exactly the behavior this
    # dependency exists for.
    scrapling_impersonate: str = "chrome"

    data_gov_in_api_key: str = ""
    data_gov_in_mca_resource_url: str = ""
    # Resource UUID for "Registrars of Companies (RoC)-wise Company Master
    # Data" on the OGD platform, extracted from the live data.gov.in resource
    # page HTML on 2026-08-04 (see docs/mca_data_access.md). This is a public
    # identifier, not a credential -- safe to default. It has NOT been
    # confirmed against an actual API response (that requires
    # DATA_GOV_IN_API_KEY, which we don't have). Override via
    # DATA_GOV_IN_MCA_RESOURCE_ID if data.gov.in republishes this dataset
    # under a different resource id, or set DATA_GOV_IN_MCA_RESOURCE_URL
    # directly to bypass this construction entirely.
    data_gov_in_mca_resource_id: str = "4dbe5667-7b6b-41d7-82af-211562424d9a"

    # FileSure (MCA registry data reseller, api.filesure.in) -- see
    # docs/filesure_data_access.md. Empty key + collection_enabled=false by
    # default; both must be explicitly set before any request is made.
    filesure_api_key: str = ""
    filesure_env: str = "sandbox"  # "sandbox" or "production" -- same base URL, different key prefix
    filesure_collection_enabled: bool = False
    filesure_base_url: str = "https://api.filesure.in/v1"

    log_level: str = "INFO"

    # Origins allowed to call the API cross-origin -- the Next.js frontend
    # dev server runs on a different port. Browsers treat localhost and
    # 127.0.0.1 as distinct origins, so both are listed here for local dev.
    # Adjust for a real deployment.
    cors_allow_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
