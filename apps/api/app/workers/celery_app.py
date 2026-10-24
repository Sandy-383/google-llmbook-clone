from typing import Any

from celery import Celery
from celery.signals import setup_logging

from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()

celery_app = Celery("llmbook", broker=settings.redis_url, include=["app.workers.tasks"])
celery_app.conf.update(
    # A task is only acknowledged once it finishes, so a crashed worker's job is redelivered.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    # Document processing gets its own queue so slow OCR jobs can't delay maintenance tasks.
    # Guides and studio outputs use the chat model, so they get their own queue, served by a
    # single-process worker: one background generation at a time on the shared GPU.
    task_routes={
        "ingest.source": {"queue": "ingest"},
        "guide.source": {"queue": "llm"},
        "studio.generate": {"queue": "llm"},
    },
    task_ignore_result=True,
    # Fail fast when publishing from the API; the stale-source sweep covers a lost enqueue.
    task_publish_retry_policy={"max_retries": 2, "interval_start": 0, "interval_step": 0.5},
    broker_connection_retry_on_startup=True,
    # Our logging already writes to stdout; redirecting it into a logger would double-wrap lines.
    worker_redirect_stdouts=False,
    timezone="UTC",
    beat_schedule={
        "cleanup-expired-sessions": {
            "task": "maintenance.cleanup_expired_sessions",
            "schedule": 3600.0,
        },
        "requeue-stale-sources": {
            "task": "ingest.requeue_stale",
            "schedule": 300.0,
        },
    },
)


@setup_logging.connect
def _configure_worker_logging(**_: Any) -> None:
    configure_logging(settings.log_level, json=settings.log_json)
