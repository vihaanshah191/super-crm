import logging
import re
import sys

from app.core.config import get_settings

_REDACT_KEYS = {"api_key", "apikey", "password", "secret", "token", "authorization"}

<<<<<<< HEAD
# Query-string secrets embedded in a URL string (e.g.
# "https://api.data.gov.in/resource/...?api-key=REALKEY&format=json") aren't
# caught by the dict-key check above, since the sensitive value lives inside
# a string under an innocuous key like "target" or "url". data.gov.in's API
# requires the key as a query param (no header alternative confirmed), so any
# log line that includes a fetch target/error message for that request can
# leak it verbatim unless string values are scanned too.
_QUERY_SECRET_PATTERN = re.compile(r"(?i)(api[-_]?key|token|secret)=[^&\s]+")


def scrub_secrets(value: str) -> str:
    """Redact api-key/token/secret query-string parameters embedded in a URL
    or error-message string. Exported so callers formatting their own
    user-facing error text (e.g. CLI commands printing a caught exception
    that embeds a fetch target URL) can apply the same redaction the logging
    filter uses, rather than leaking a credential outside the log pipeline."""
    return _QUERY_SECRET_PATTERN.sub(r"\1=***", value)
=======
# Secrets embedded inside a string value under an innocuous key (e.g. "url"
# or "error") aren't caught by the dict-key check above. Two shapes are
# covered: URL query params (data.gov.in's "?api-key=REALKEY") and
# header-style key/value text such as a stringified headers dict or an
# "x-api-key: REALKEY" fragment (FileSure's auth header -- see
# app/source_adapters/filesure_client.py) -- both `key=value` and
# `'key': 'value'` / `key: value` separators are matched, with an optional
# "x-" prefix on the key name, since that's the real FileSure header name.
_QUERY_SECRET_PATTERN = re.compile(
    r"""(?i)((?:x-)?(?:api[-_]?key|token|secret))['"]?\s*[:=]\s*['"]?[^&\s'"]+"""
)


def scrub_secrets(value: str) -> str:
    """Redact api-key/token/secret values embedded in a URL, header
    fragment, or stringified headers dict inside an error-message string.
    Exported so callers formatting their own user-facing error text (e.g.
    CLI commands printing a caught exception that embeds a fetch target URL
    or headers) can apply the same redaction the logging filter uses,
    rather than leaking a credential outside the log pipeline."""
    return _QUERY_SECRET_PATTERN.sub(lambda m: f"{m.group(1)}=***", value)
>>>>>>> 3698f6932ecf2969d1d18f2fc5466ee0f4fd2b55


class RedactingFilter(logging.Filter):
    """Strips known-sensitive keys/values out of structured logging kwargs
    before they hit stdout -- both whole dict values under a sensitive key
    name, and sensitive query-string parameters embedded inside a URL or
    error-message string under an unrelated key name."""

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "extra_fields") and isinstance(record.extra_fields, dict):
            record.extra_fields = {
                k: ("***" if k.lower() in _REDACT_KEYS else (scrub_secrets(v) if isinstance(v, str) else v))
                for k, v in record.extra_fields.items()
            }
        if isinstance(record.msg, str):
            record.msg = scrub_secrets(record.msg)
        return True


def configure_logging() -> None:
    settings = get_settings()
<<<<<<< HEAD
    # Windows consoles default to a legacy codepage (e.g. cp1252) that can't
    # encode arbitrary non-ASCII log content (company names, "₹", etc.);
    # force UTF-8 so logging never crashes on it.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
=======
>>>>>>> 3698f6932ecf2969d1d18f2fc5466ee0f4fd2b55
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
