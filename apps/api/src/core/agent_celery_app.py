"""Isolated Celery app for customer replies only.

Production intentionally runs this worker without importing or consuming the
campaign/discovery queues. This prevents enabling the WhatsApp bot from
activating unrelated dormant automation.
"""

from celery import Celery

from .config import get_settings

_settings = get_settings()
_configuration_errors = _settings.production_runtime_errors()
if _configuration_errors:
    raise RuntimeError(
        "Production runtime configuration is invalid: " + "; ".join(_configuration_errors)
    )

agent_celery_app = Celery(
    "leadpulse_agent_runtime",
    broker=str(_settings.celery_broker_url),
    backend=str(_settings.celery_result_backend),
    include=["src.workers.agent_runtime", "src.workers.knowledge"],
)

agent_celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=180,
    task_soft_time_limit=150,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=500,
    task_default_queue="agent_runtime",
    task_routes={
        "src.workers.agent_runtime.*": {"queue": "agent_runtime"},
        # Knowledge indexing and memory enrichment never block a customer
        # reply: they run on their own queue (``-Q agent_runtime,knowledge``).
        "src.workers.knowledge.*": {"queue": "knowledge"},
    },
)

agent_celery_app.conf.beat_schedule = {
    "recover-pending-agent-runtime-jobs": {
        "task": "src.workers.agent_runtime.dispatch_pending_runtime_jobs",
        "schedule": 15.0,
    }
}
