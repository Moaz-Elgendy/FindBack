"""The Celery worker must actually be able to run `process_item`.

docker-compose starts the worker as

    celery -A app.celery_app.celery worker --loglevel=info

which imports only `app.celery_app`. Nothing in that process imports
`app.tasks`, so unless the Celery app names the task module in `include`, the
`@celery.task` decorator never runs and the worker's registry is empty. The API
then publishes `process_item` successfully, the worker rejects every message
with `NotRegistered`, and no save is ever processed.

Why the subprocess
------------------
Asserting on this process's registry would prove nothing: pytest imports the
other test modules first, most of which import `app.main`, which imports
`app.tasks`. `process_item` is therefore registered by the time any test body
runs whether or not `include` is set. Only a fresh interpreter that imports
`app.celery_app` alone reproduces what the worker sees.
"""
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

WORKER_STARTUP = (
    "from app.celery_app import celery; "
    "celery.loader.import_default_modules(); "
    "print('process_item' in celery.tasks)"
)


def test_the_celery_app_names_the_task_module():
    """The configuration a worker acts on, checked in-process."""
    from app.celery_app import celery

    assert "app.tasks" in tuple(celery.conf.include or ()), (
        "app.tasks is not in the Celery app's include list, so a worker "
        "process would start with an empty task registry")


def test_a_worker_startup_registers_process_item():
    """What the worker sees: a clean interpreter that imports only the app."""
    env = dict(os.environ)
    env.setdefault("DATABASE_URL", "postgresql://x:x@localhost:5432/x")
    result = subprocess.run(
        [sys.executable, "-c", WORKER_STARTUP],
        cwd=str(BACKEND), capture_output=True, text=True, timeout=120, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("True"), (
        f"the worker's registry did not contain process_item: {result.stdout!r} "
        f"{result.stderr!r}")


def test_publishing_does_not_contact_the_result_backend():
    """A save must reach the broker without a synchronous result-backend hop.

    `Celery.send_task` calls `self.backend.on_task_call(...)` before publishing
    unless results are ignored (celery/app/base.py:797). For the Redis backend
    that subscribes to the task's result channel, which opens a Redis connection
    with a 20 x 1s retry policy. Measured with Redis down:

        process_item.delay(...)   ->  OperationalError after 19.09 s
        with task_ignore_result   ->  OperationalError after  0.69 s

    Nothing here reads a task result -- success and failure are recorded in
    `processing_jobs` -- so the hop is pure cost, and 19 s per save is an
    outage-shaped bug in the path that is supposed to be instant.
    """
    from app.celery_app import celery

    assert celery.conf.task_ignore_result is True, (
        "task_ignore_result is off, so every publish waits on the Redis result "
        "backend before it tries the broker")
