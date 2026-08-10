"""Per-source compliance controls: the enable/disable switch, rate limits, and
the audit trail (robots/license/retention notes) required before a source may
be collected at all.

This module is deliberately DB-agnostic (SourcePolicy is a plain dataclass, not
the SQLAlchemy Source model) so adapters and tests can exercise policy checks
without a database.
"""

import threading
import time
from dataclasses import dataclass


class CollectionNotPermittedError(RuntimeError):
    """Raised when a source is disabled or a call would exceed its rate limit."""


@dataclass(frozen=True)
class SourcePolicy:
    source_name: str
    collection_enabled: bool
    rate_limit_per_minute: int
    max_concurrency: int
    license_notes: str = ""
    robots_notes: str = ""

    def assert_collection_allowed(self) -> None:
        if not self.collection_enabled:
            raise CollectionNotPermittedError(
                f"Collection is disabled for source '{self.source_name}'. "
                "A source must have an explicitly confirmed permitted access "
                "method (API terms reviewed, robots directives checked, license "
                "confirmed) before collection_enabled is set to True."
            )


class RateLimiter:
    """A simple in-memory token-bucket rate limiter, one bucket per source name.

    Sufficient for a single-process worker / the PoC. A multi-worker Celery
    deployment should back this with a shared store (e.g. Redis) instead --
    swap this class out, the SourceAdapter/collector interface doesn't change.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, list[float]] = {}

    def allow(self, source_name: str, limit_per_minute: int) -> bool:
        now = time.monotonic()
        window_start = now - 60.0
        with self._lock:
            timestamps = self._buckets.setdefault(source_name, [])
            timestamps[:] = [t for t in timestamps if t > window_start]
            if len(timestamps) >= limit_per_minute:
                return False
            timestamps.append(now)
            return True


rate_limiter = RateLimiter()
