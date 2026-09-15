"""Celery application.

Started via: `celery -A src.core.celery_app.celery_app worker -l info`
Beat scheduler: `celery -A src.core.celery_app.celery_app beat -l info`
"""

from datetime import timedelta

from celery import Celery
from celery.schedules import crontab

from .config import get_settings

_settings = get_settings()

celery_app = Celery(
    "leadpulse",
    broker=str(_settings.celery_broker_url),
    backend=str(_settings.celery_result_backend),
    include=[
        "src.workers.discovery",
        "src.workers.enrichment",
        "src.workers.outreach",
        "src.workers.maintenance",
        "src.workers.campaign_imports",
        "src.workers.campaign_outbox",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=600,
    task_soft_time_limit=540,
    worker_prefetch_multiplier=4,
    worker_max_tasks_per_child=1000,
)

celery_app.conf.beat_schedule = {
    "outreach-dispatcher-every-minute": {
        "task": "src.workers.outreach.dispatch_outreach",
        "schedule": crontab(minute="*"),
    },
    "sender-warmup-daily-reset": {
        "task": "src.workers.maintenance.reset_daily_send_counters",
        "schedule": crontab(hour=0, minute=5),
    },
    "compliance-iys-refresh": {
        "task": "src.workers.maintenance.refresh_iys_cache",
        "schedule": crontab(hour=3, minute=0),
    },
    "campaign-import-recovery-every-minute": {
        "task": "src.workers.campaign_imports.dispatch_queued_campaign_imports",
        "schedule": crontab(minute="*"),
    },
    # File batches are already durable in PostgreSQL.  A short bounded scan
    # starts/repairs their chunked outbox work without ever enqueueing before
    # the workflow transaction commits.
    "campaign-outbox-progress-every-five-seconds": {
        "task": "src.workers.campaign_outbox.progress_campaign_outbox",
        "schedule": timedelta(seconds=5),
    },
    # Keep raw phone files/rows within a small, explicit window after their
    # 30-day expiry. The bucket lifecycle is a second safety net, not the
    # primary schedule; daily cleanup could otherwise retain PII for almost
    # one extra day.
    "campaign-import-retention-cleanup-every-fifteen-minutes": {
        "task": "src.workers.campaign_imports.cleanup_expired_campaign_imports",
        "schedule": timedelta(minutes=15),
    },
}
