import asyncio
from app.services.extractor import extract_memory


def test_heuristic_recipe_without_credentials(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = asyncio.run(extract_memory("Cook chicken with cream and mushrooms", "Dinner"))
    assert result.category == "recipe"
    assert result.summary
