"""Provider-response parsing and JSON repair. Pure functions, no I/O."""
import math

import pytest

from app.services import ai


# --- completion_text: two response dialects ---------------------------------

def test_completion_text_reads_openai_shape():
    data = {"choices": [{"message": {"content": "hello"}}]}
    assert ai.completion_text(data, "groq") == "hello"


def test_completion_text_reads_content_parts():
    data = {"choices": [{"message": {"content": [{"type": "text", "text": "a"},
                                                 {"type": "text", "text": "b"}]}}]}
    assert ai.completion_text(data, "openai") == "ab"


def test_completion_text_reads_gemini_native_parts():
    data = {"candidates": [{"content": {"parts": [{"text": "hi "}, {"text": "there"}]}}]}
    assert ai.completion_text(data, "gemini") == "hi there"


def test_completion_text_gemini_safety_block_is_an_error_not_empty_text():
    data = {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]}
    with pytest.raises(ai.AIProtocolError) as excinfo:
        ai.completion_text(data, "gemini")
    assert "safety" in str(excinfo.value).lower()


def test_completion_text_gemini_prompt_block_is_an_error():
    data = {"candidates": [], "promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}
    with pytest.raises(ai.AIProtocolError):
        ai.completion_text(data, "gemini")


def test_completion_text_distinguishes_token_limit_from_empty():
    data = {"choices": [{"message": {"content": None}, "finish_reason": "length"}]}
    with pytest.raises(ai.AIProtocolError) as excinfo:
        ai.completion_text(data, "groq")
    assert "EXTRACTOR_MAX_TOKENS" in str(excinfo.value)


def test_completion_text_reports_unknown_shape():
    with pytest.raises(ai.AIProtocolError) as excinfo:
        ai.completion_text({"structured_data": [1, 2]}, "groq")
    assert "structured_data" in str(excinfo.value)


# --- parse_json_object: the repair ladder -----------------------------------

def test_parse_json_plain():
    assert ai.parse_json_object('{"a": 1}') == {"a": 1}


def test_parse_json_inside_markdown_fence():
    assert ai.parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_surrounded_by_prose():
    assert ai.parse_json_object('Sure! {"a": 1} Hope that helps.') == {"a": 1}


def test_parse_json_strips_reasoning_block():
    assert ai.parse_json_object('<think>hmm</think>{"a": 1}') == {"a": 1}


def test_parse_json_removes_trailing_commas():
    assert ai.parse_json_object('{"a": [1, 2,], "b": 1,}') == {"a": [1, 2], "b": 1}


def test_parse_json_closes_object_truncated_by_token_limit():
    assert ai.parse_json_object('{"summary": "the page is abou') == {
        "summary": "the page is abou"}


def test_parse_json_closes_truncated_array():
    assert ai.parse_json_object('{"tags": ["a", "b"') == {"tags": ["a", "b"]}


def test_parse_json_unwraps_array_and_double_encoding():
    assert ai.parse_json_object('[{"a": 1}]') == {"a": 1}
    assert ai.parse_json_object('"{\\"a\\": 1}"') == {"a": 1}


def test_parse_json_keeps_braces_inside_strings():
    assert ai.parse_json_object('{"a": "}{"}') == {"a": "}{"}


@pytest.mark.parametrize("raw", ["", "   ", "no json here", "42", "[1, 2, 3]"])
def test_parse_json_rejects_unusable_text(raw):
    with pytest.raises(ai.AIJSONError):
        ai.parse_json_object(raw)


def test_parse_json_error_message_shows_what_the_model_sent():
    with pytest.raises(ai.AIJSONError) as excinfo:
        ai.parse_json_object("totally not json", "groq")
    assert "totally" in str(excinfo.value)


# --- vectors -----------------------------------------------------------------

def test_normalize_produces_unit_length():
    vector = ai._normalize([3.0, 4.0])
    assert math.isclose(math.hypot(*vector), 1.0)


def _cfg(dims=3):
    return ai.EmbeddingConfig(provider="gemini", api_key="k", model="m",
                              style="gemini_native", url="u", dims=dims,
                              normalize=False)


def test_guard_dimensions_refuses_wrong_width():
    assert ai._guard_dimensions([0.1, 0.2], _cfg(dims=3), 0) is None


def test_guard_dimensions_refuses_empty_vector():
    assert ai._guard_dimensions([], _cfg(dims=3), 0) is None
    assert ai._guard_dimensions(None, _cfg(dims=3), 0) is None


def test_guard_dimensions_keeps_matching_width():
    assert ai._guard_dimensions([1, 2, 3], _cfg(dims=3), 0) == [1.0, 2.0, 3.0]


def test_guard_dimensions_normalizes_when_provider_asks_for_it():
    cfg = ai.EmbeddingConfig(provider="gemini", api_key="k", model="m", style="gemini_native",
                             url="u", dims=2, normalize=True)
    assert math.isclose(math.hypot(*ai._guard_dimensions([3, 4], cfg, 0)), 1.0)


# --- request-shape diagnostics ----------------------------------------------

@pytest.mark.parametrize("message,expected", [
    ('Unknown name "responseMimeType" at #object', "response_format"),
    ("Unsupported parameter: response_format", "response_format"),
    ('Unexpected field "max_output_tokens"', "max_tokens"),
    ('Unrecognized key: "top_p"', "top_p"),
    ('Invalid JSON payload received. Unknown name "taskType"', "taskType"),
    ("Temperature must be between 0 and 2", "temperature"),
    ("Requests exceeding 1MB are rejected", ""),
])
def test_rejected_param_names_the_field_the_provider_complained_about(message, expected):
    assert ai._rejected_param(message) == expected


def test_retry_delay_honours_retry_after_header():
    response = type("R", (), {"headers": {"retry-after": "3"}})()
    assert ai._retry_delay(response, 1) == 3.0


def test_retry_delay_backs_off_exponentially():
    first, later = ai._retry_delay(None, 1), ai._retry_delay(None, 4)
    assert 0 < first <= ai.BACKOFF_BASE * 1.2 < later <= ai.BACKOFF_CAP * 1.2
