import os
from celery import Celery
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
                   task_ignore_result=True)


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
