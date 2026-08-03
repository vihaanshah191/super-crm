import logging
import sys

from app.core.config import get_settings

_REDACT_KEYS = {"api_key", "apikey", "password", "secret", "token", "authorization"}


class RedactingFilter(logging.Filter):
    """Strips known-sensitive keys out of structured logging kwargs before they hit stdout."""

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "extra_fields") and isinstance(record.extra_fields, dict):
            record.extra_fields = {
                k: ("***" if k.lower() in _REDACT_KEYS else v)
                for k, v in record.extra_fields.items()
            }
        return True


def configure_logging() -> None:
    settings = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingFilter())
    formatter = logging.Formatter(
        fmt='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}'
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
