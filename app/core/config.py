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

    data_gov_in_api_key: str = ""
    data_gov_in_mca_resource_url: str = ""

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
