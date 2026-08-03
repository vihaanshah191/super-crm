from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "super_crm",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.ingestion.jobs.tasks"],
)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_retry_delay=30,
    task_time_limit=600,
    task_soft_time_limit=540,
)

# Example schedule -- real source cadences belong in the Source row's own
# config once that's exposed via an admin UI/API; this is deliberately a
# small static example for the PoC, not a claim about production cadence.
celery_app.conf.beat_schedule = {
    "dispatch-daily-source-collections": {
        "task": "app.ingestion.jobs.tasks.dispatch_enabled_source_collections",
        "schedule": crontab(hour=2, minute=0),
    },
}
