"""Test setup must reject developer database targets before migrations run."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from app.database import alembic_config

BACKEND = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("target", ["", "findback", "postgres"])
def test_pytest_refuses_missing_or_unsafe_test_database(target):
    env = dict(os.environ)
    env.pop("TEST_DATABASE_URL", None)
    if target:
        env["TEST_DATABASE_URL"] = "postgresql://x:x@localhost/" + target
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "tests/test_canonical.py"], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "TEST_DATABASE_URL" in result.stderr


@pytest.mark.parametrize("action", ["upgrade", "downgrade", "stamp"])
@pytest.mark.parametrize("env_override", [False, True])
def test_migrations_refuse_unsafe_resolved_database(monkeypatch, action, env_override):
    cfg = alembic_config()
    unsafe = "postgresql://x:x@invalid.invalid/findback"
    if env_override:
        monkeypatch.setenv("DATABASE_URL", unsafe)
        cfg.set_main_option("sqlalchemy.url", os.environ["TEST_DATABASE_URL"])
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        cfg.set_main_option("sqlalchemy.url", unsafe)
    with pytest.raises(pytest.UsageError, match="Refusing.*findback"):
        getattr(command, action)(cfg, "head")
