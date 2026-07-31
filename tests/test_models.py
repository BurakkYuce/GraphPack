"""Provider-specific extraction settings.

Every assertion here encodes something that was measured rather than read.
Getting any of it wrong produces an ingest that finishes cleanly and extracts
nothing, so the cases are pinned rather than left to memory.
"""

from __future__ import annotations

import pytest

from graphpack.models import (
    DEFAULT_LOCAL_CONTEXT_WINDOW,
    extractor_type_for,
    properties_supported,
    tune_llm,
)

pytestmark = pytest.mark.unit


class FakeLLM:
    """Stands in for the engine's LLM object — the two fields tune_llm touches.

    The default of -1 is what LlamaIndex's Ollama actually holds until its first
    call: the engine passes no context window, so the value stays unresolved
    until the model is asked what it supports.
    """

    def __init__(self, context_window: int = -1, is_function_calling_model: bool = True):
        self.context_window = context_window
        self.is_function_calling_model = is_function_calling_model


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("ollama", "dynamic"),
        ("openai", "schema"),
        ("anthropic", "schema"),
        (None, "schema"),
    ],
)
def test_extractor_choice_follows_the_provider(provider, expected):
    """SchemaLLMPathExtractor constrains extraction to the ontology's types and
    is preferred; Ollama's tool calling cannot drive it, and produces zero
    entities without raising anything."""
    assert extractor_type_for(provider) == expected


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("ollama", False), ("openai", True), (None, True)],
)
def test_properties_are_off_only_where_the_dynamic_path_breaks_them(provider, expected):
    """With any property list set, DynamicLLMPathExtractor binds the
    with-properties prompt at construction, but LlamaIndex only takes the
    with-properties path when *both* lists are non-None. An ontology with entity
    properties and no relation properties therefore renders
    `{allowed_entity_properties}` into the prompt as literal text."""
    assert properties_supported(provider) == expected


def test_local_models_get_a_context_window_they_can_afford():
    """llama3.1 advertises 131,072 tokens and Ollama sizes its KV cache to
    match. Extraction chunks are about a kilobyte; the difference was 118
    seconds a chunk against 34."""
    llm = FakeLLM(context_window=131072)

    tune_llm(llm, "ollama")

    assert llm.context_window == DEFAULT_LOCAL_CONTEXT_WINDOW


def test_an_unresolved_context_window_is_still_set():
    """LlamaIndex holds -1 until the first call, then asks the model and caches
    the answer. A rule that only shrinks values it can already see never fires,
    and the model loads at its advertised size — which is how this was missed
    the first time."""
    llm = FakeLLM(context_window=-1)

    tune_llm(llm, "ollama")

    assert llm.context_window == DEFAULT_LOCAL_CONTEXT_WINDOW


def test_a_context_window_that_is_already_small_is_left_alone():
    llm = FakeLLM(context_window=4096)

    tune_llm(llm, "ollama")

    assert llm.context_window == 4096


def test_context_window_is_configurable(monkeypatch):
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "16384")
    llm = FakeLLM(context_window=131072)

    tune_llm(llm, "ollama")

    assert llm.context_window == 16384


def test_function_calling_is_turned_off_for_the_dynamic_path():
    """DynamicLLMPathExtractor calls apredict(), which expects plain text. With
    tools on, achat() answers with a tool call whose content is None and
    apredict() collapses it to '' — silently, and with no entities."""
    llm = FakeLLM(is_function_calling_model=True)

    tune_llm(llm, "ollama")

    assert llm.is_function_calling_model is False


def test_hosted_providers_are_left_untouched():
    """They drive the schema extractor correctly; changing either field would
    break the better path to fix the worse one."""
    llm = FakeLLM(context_window=131072, is_function_calling_model=True)

    tune_llm(llm, "openai")

    assert llm.context_window == 131072
    assert llm.is_function_calling_model is True


def test_provider_may_arrive_as_an_enum():
    """The engine's Settings stores providers as enum values or plain strings
    depending on how they were set."""

    class Provider:
        value = "ollama"

    assert extractor_type_for(Provider()) == "dynamic"
