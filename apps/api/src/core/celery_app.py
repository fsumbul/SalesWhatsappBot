"""Celery application.

Started via: `celery -A src.core.celery_app.celery_app worker -l info`
Beat scheduler: `celery -A src.core.celery_app.celery_app beat -l info`
"""

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
}
