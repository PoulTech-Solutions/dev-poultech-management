from celery import Celery
from celery.schedules import crontab
from kombu import Exchange, Queue

from app.core.config import get_settings
from app.core.logging import setup_logging

settings = get_settings()
setup_logging(settings.log_level, json_logs=settings.is_prod)


def _queue(name: str) -> Queue:
    # Each queue needs its own exchange/routing_key. With only Queue(name) and
    # task_default_queue='scheduled', Celery binds every queue to exchange=scheduled,
    # so email tasks can miss the worker.
    return Queue(name, Exchange(name, type='direct'), routing_key=name)


celery_app = Celery(
    'poultech',
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=['app.tasks.email', 'app.tasks.market'],
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=60,
    task_soft_time_limit=45,
    result_expires=3600,
    # Nothing in this batch reads a task result, and storing one makes the *producer*
    # open a connection to the result backend while publishing: with the backend down,
    # that turned a mail outage into a 20-second hang inside /auth/signup.
    task_ignore_result=True,
    # Producer-side rules. The worker keeps retrying (broker_connection_retry_on_startup),
    # but a request handler must fail fast and let the caller answer 503: `true` here
    # made an unreachable broker hang signup for minutes.
    broker_connection_retry=False,
    broker_connection_timeout=2,
    task_publish_retry=False,
    broker_connection_retry_on_startup=True,
    task_default_queue='scheduled',
    task_default_exchange='scheduled',
    task_default_routing_key='scheduled',
    task_queues=(
        _queue('email'),
        _queue('notifications'),
        _queue('billing'),
        _queue('reports'),
        _queue('scheduled'),
    ),
    task_routes={
        'app.tasks.email.*': {'queue': 'email'},
        'app.tasks.market.*': {'queue': 'scheduled'},
    },
    beat_schedule={
        'market-sync-necc-egg-rates': {
            'task': 'app.tasks.market.sync_necc_egg_rates',
            'schedule': crontab(
                minute=settings.market_sync_cron_minute,
                hour=settings.market_sync_cron_hour,
            ),
            'options': {'queue': 'scheduled'},
        },
    },
    # In test the task runs inline: no broker round trip, and a mail failure
    # surfaces as the assertion instead of a 20-second connect retry.
    task_always_eager=settings.app_env == 'test',
    task_eager_propagates=settings.app_env == 'test',
    broker_transport_options={
        'max_retries': 1,
        'interval_start': 0,
        'interval_step': 0.2,
        'interval_max': 0.5,
    },
)
