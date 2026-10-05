"""API startup must not migrate the Compose development database."""
import json
import subprocess
from pathlib import Path

from app import database


def test_none_bootstrap_does_not_migrate_or_create(monkeypatch):
    monkeypatch.setenv("SCHEMA_BOOTSTRAP", "none")

    def forbidden():
        raise AssertionError("schema bootstrap touched the database")

    monkeypatch.setattr(database, "upgrade_head", forbidden)
    monkeypatch.setattr(database, "create_all", forbidden)
    assert database.init_db() == "skipped"


def test_resolved_compose_api_disables_bootstrap():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["docker", "compose", "config", "--no-env-resolution", "--format", "json"],
        cwd=root, capture_output=True, text=True, check=True, timeout=30,
    )
    config = json.loads(result.stdout)
    assert config["services"]["api"]["environment"].get("SCHEMA_BOOTSTRAP") == "none"
