"""Preflight checks.

`doctor` gates an ingest that runs for hours, so a check that passes when it
should not is worse than no check. These cover the reporting logic; whether a
given service is up is not something a test can assert.
"""

from __future__ import annotations

import pytest

from graphpack.doctor import _check_embedding, _check_llm, _has_model

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "LLM_PROVIDER",
        "EMBEDDING_KIND",
        "EMBEDDING_MODEL",
        "OLLAMA_MODEL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_a_missing_api_key_fails_and_names_the_variable(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    check = _check_llm()

    assert not check.ok
    assert "OPENAI_API_KEY" in check.detail
    assert "OPENAI_API_KEY" in check.fix


def test_a_present_api_key_passes(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    assert _check_llm().ok


def test_a_provider_that_serves_no_embeddings_fails_even_with_its_key_set(monkeypatch):
    """Anthropic, Groq and OpenRouter publish no embedding model. Having a valid
    key for one says nothing about whether chunks can be embedded, and the
    failure would otherwise land after the documents were already fetched."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    assert _check_llm().ok
    check = _check_embedding()
    assert not check.ok
    assert "no embedding model" in check.detail


def test_pairing_a_second_provider_for_embeddings_passes(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("EMBEDDING_KIND", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    assert _check_embedding().ok


def test_embedding_falls_back_to_the_llm_provider(monkeypatch):
    """EMBEDDING_KIND unset means "same place as the LLM", which is what the
    engine assumes too."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    check = _check_embedding()

    assert check.ok
    assert "openai" in check.detail


def test_ollama_reports_a_model_that_is_not_pulled(monkeypatch):
    """A running server with the wrong model is the failure that looks like
    success — the connection works, and extraction gets nothing."""
    monkeypatch.setattr("graphpack.doctor._ollama_models", lambda _: (["other:latest"], ""))
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1:8b")

    check = _check_llm()

    assert not check.ok
    assert "ollama pull llama3.1:8b" == check.fix


def test_ollama_reports_an_unreachable_server(monkeypatch):
    monkeypatch.setattr("graphpack.doctor._ollama_models", lambda _: ([], "not reachable"))
    monkeypatch.setenv("LLM_PROVIDER", "ollama")

    check = _check_llm()

    assert not check.ok
    assert "brew services start ollama" in check.fix


def test_a_pulled_model_passes(monkeypatch):
    monkeypatch.setattr("graphpack.doctor._ollama_models", lambda _: (["llama3.1:8b"], ""))
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1:8b")

    assert _check_llm().ok


@pytest.mark.parametrize(
    ("wanted", "available", "expected"),
    [
        ("nomic-embed-text", ["nomic-embed-text:latest"], True),
        ("llama3.1:8b", ["llama3.1:8b"], True),
        ("llama3.1", ["llama3.1:8b"], False),
        ("missing", ["llama3.1:8b"], False),
    ],
)
def test_model_names_account_for_the_implicit_latest_tag(wanted, available, expected):
    """`ollama pull nomic-embed-text` lists as `nomic-embed-text:latest`."""
    assert _has_model(wanted, available) is expected


def test_the_gemini_key_variable_is_the_one_the_engine_reads():
    """`config.Settings` reads GEMINI_API_KEY for the gemini LLM. The embedding
    path for the same vendor accepts GOOGLE_API_KEY too, and naming that one
    here failed a correctly configured run — telling it to set a variable the
    LLM path never looks at."""
    from graphpack.doctor import _API_KEY_VARS

    assert _API_KEY_VARS["gemini"] == "GEMINI_API_KEY"
