"""The connectivity-check script stays importable and correctly wired.

It is the command the extractor's "model not found" hint tells an operator to
run, so this guards it against bit-rot: a syntax error or a provider-picking
regression here would send them to a script that cannot even start.
"""
import importlib.util
import asyncio
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_ai.py"

_spec = importlib.util.spec_from_file_location("check_ai", SCRIPT)
check_ai = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_ai)


def test_models_url_sits_beside_the_chat_endpoint():
    assert check_ai._models_url("https://api.groq.com/openai/v1/chat/completions") == \
        "https://api.groq.com/openai/v1/models"
    assert check_ai._models_url("https://api.openai.com/v1/chat/completions") == \
        "https://api.openai.com/v1/models"


def test_check_ai_fails_cleanly_without_a_provider(offline_providers, capsys):
    assert asyncio.run(check_ai.main(["check_ai.py"])) == 1
    out = capsys.readouterr().out.lower()
    assert "no provider key is configured" in out
