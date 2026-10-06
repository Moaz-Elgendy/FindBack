"""The outbox dispatcher has to be configurable and actually started.

Phase 5 makes a stranded processing job durable. Nothing ran the dispatcher, so
in practice a save taken while Redis was down stayed PENDING forever: the entry
point existed, was tested, and was never invoked by the deployed stack.

These two checks need no database, so they run in every configuration.
"""
from pathlib import Path

import pytest
import yaml

from app.services import outbox

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"


def test_the_dispatcher_interval_comes_from_the_environment(monkeypatch):
    """The tick length is an operator setting, not a constant in the code."""
    monkeypatch.delenv("OUTBOX_DISPATCH_INTERVAL_SECONDS", raising=False)
    assert outbox.dispatch_interval() == outbox.DEFAULT_DISPATCH_INTERVAL_SECONDS
    assert outbox.DEFAULT_DISPATCH_INTERVAL_SECONDS == 5, "documented default"

    monkeypatch.setenv("OUTBOX_DISPATCH_INTERVAL_SECONDS", "30")
    assert outbox.dispatch_interval() == 30

    # Zero would spin the loop against the database as fast as it can connect.
    monkeypatch.setenv("OUTBOX_DISPATCH_INTERVAL_SECONDS", "0")
    assert outbox.dispatch_interval() == 1

    monkeypatch.setenv("OUTBOX_DISPATCH_INTERVAL_SECONDS", "-4")
    assert outbox.dispatch_interval() == 1

    monkeypatch.setenv("OUTBOX_DISPATCH_INTERVAL_SECONDS", "every-five-seconds")
    assert outbox.dispatch_interval() == outbox.DEFAULT_DISPATCH_INTERVAL_SECONDS


def test_the_entry_point_reads_the_interval_from_the_environment(monkeypatch):
    """`--interval` stays available as an override; the env is the default."""
    import argparse

    from scripts import dispatch_outbox

    monkeypatch.setenv("OUTBOX_DISPATCH_INTERVAL_SECONDS", "17")
    # A fresh parser each time: argparse captures the default when it is built,
    # which is exactly the behaviour under test.
    assert dispatch_outbox.build_parser().parse_args([]).interval == 17
    assert (dispatch_outbox.build_parser()
            .parse_args(["--interval", "3"]).interval == 3), "CLI wins"

    monkeypatch.delenv("OUTBOX_DISPATCH_INTERVAL_SECONDS", raising=False)
    assert (dispatch_outbox.build_parser().parse_args([]).interval
            == outbox.DEFAULT_DISPATCH_INTERVAL_SECONDS)


def test_the_deployment_starts_the_dispatcher():
    """docker-compose is where the worker is started; the dispatcher too.

    A loop that no process runs is the same as no dispatcher: the job a queue
    outage left behind is never republished and the save never gets processed.
    """
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]

    dispatchers = {
        name: spec for name, spec in services.items()
        if "scripts.dispatch_outbox" in str((spec or {}).get("command", ""))
    }
    assert dispatchers, (
        "no docker-compose service runs scripts.dispatch_outbox, so a job "
        "stranded by a queue outage is never republished")

    spec = next(iter(dispatchers.values()))
    env = spec.get("environment") or {}
    assert "DATABASE_URL" in env, "the dispatcher must read processing_jobs"
    assert "REDIS_URL" in env, "the dispatcher publishes to the Celery broker"
    assert spec.get("env_file"), "the dispatcher needs the same .env as the API"
    assert spec.get("depends_on"), "the dispatcher must wait for its dependencies"


def test_the_worker_and_the_dispatcher_agree_on_the_broker():
    """Same REDIS_URL in both, or the dispatcher publishes into the void."""
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]

    def env_of(predicate):
        for spec in services.values():
            if predicate(spec or {}):
                return (spec.get("environment") or {})
        return None

    worker = env_of(lambda s: "celery" in str(s.get("command", ""))
                    and "worker" in str(s.get("command", "")))
    dispatcher = env_of(
        lambda s: "scripts.dispatch_outbox" in str(s.get("command", "")))
    assert worker is not None, "no worker service in docker-compose.yml"
    assert dispatcher is not None
    assert worker["REDIS_URL"] == dispatcher["REDIS_URL"]
    assert worker["DATABASE_URL"] == dispatcher["DATABASE_URL"]


def test_postgres_host_port_does_not_conflict_with_host_postgresql():
    services = yaml.safe_load(COMPOSE.read_text(encoding='utf-8'))['services']
    assert services['postgres']['ports'] == ['127.0.0.1:55433:5432']
    for name in ('api', 'worker', 'outbox'):
        assert services[name]['environment']['DATABASE_URL'].endswith('@postgres:5432/findback')
