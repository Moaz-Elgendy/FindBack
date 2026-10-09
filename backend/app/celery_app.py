import os
from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# `include` is what registers the task with the worker. Without it the
# worker process imports only this module, the @celery.task decorator in
# app/tasks.py never runs, and every published job fails NotRegistered.
celery = Celery("findback", broker=REDIS_URL, backend=REDIS_URL,
                include=["app.tasks"])
celery.conf.update(task_serializer="json", accept_content=["json"],
                   result_serializer="json", timezone="UTC",
                   # Nothing reads a task result: success and failure live in
                   # `processing_jobs`. Leaving this False makes every publish
                   # call `backend.on_task_call()` first, which subscribes to the
                   # Redis result channel and retries a dead connection 20 times --
                   # a ~19 s stall per save while Redis is down, before the broker
                   # is even tried. The backend stays configured for /health.
                   task_ignore_result=True,
                   # Jobs are long (fetch, transcribe, call a model). With the
                   # default prefetch of 4 a busy worker hoards three more jobs
                   # that an idle one could be running.
                   worker_prefetch_multiplier=1,
                   # A provider call that never returns would otherwise pin a
                   # worker forever. The soft limit raises inside the task, so
                   # it is recorded as a failure and retried; the hard limit
                   # kills the process if that does not unwedge it. Both sit
                   # well above a normal run; override per deployment with
                   # TASK_SOFT_TIME_LIMIT / TASK_TIME_LIMIT (seconds).
                   task_soft_time_limit=int(os.getenv("TASK_SOFT_TIME_LIMIT", "900")),
                   task_time_limit=int(os.getenv("TASK_TIME_LIMIT", "960")),
                   # The weekly note. Beat fires this every fifteen minutes;
                   # the task itself decides whose chosen moment that tick
                   # covers, because the user's day/hour/minute are in their
                   # own saved zone and cannot be expressed as a UTC schedule.
                   #
                   # Requires a `celery beat` process alongside the worker --
                   # the worker alone never runs a beat_schedule entry.
                   beat_schedule={
                       'weekly-note': {
                           'task': 'run_weekly_note',
                           'schedule': crontab(minute='*/15'),
                       },
                   })


@worker_process_init.connect
def _install_worker_log_filters(**_kwargs) -> None:
    """Install the log filters in every worker process.

    A Celery worker never imports `app.main`, so without this the process that
    fetches pages, calls the model and embeds text would log the user's content
    unfiltered. `worker_process_init` runs once per prefork child, after Celery
    has configured its handlers.
    """
    from app.log_filters import install_log_filters

    install_log_filters()
