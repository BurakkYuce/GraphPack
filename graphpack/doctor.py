"""Check that everything an ingest needs is actually reachable.

Ingest is the expensive step — minutes of downloads, then hours of extraction —
and the ways it fails are mostly unglamorous: a container that was never
started, a model that was never pulled, an API key that is not set. Each of
those produces an error somewhere deep in the engine, long after the point where
it could have been reported in one line.

Every check answers the same question: would this stop an ingest, and what does
the reader do about it.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

TIMEOUT = 10


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def run_checks() -> list[Check]:
    return [
        _check_engine(),
        _check_neo4j(),
        _check_qdrant(),
        _check_llm(),
        _check_embedding(),
    ]


def _check_engine() -> Check:
    try:
        import config  # engine module, flat top-level name

        version = getattr(config, "__version__", "") or "installed"
        return Check("engine", True, f"flexible-graphrag {version}")
    except ImportError as exc:
        return Check(
            "engine",
            False,
            str(exc),
            "uv pip install -e ../flexible-graphrag/flexible-graphrag",
        )


def _check_neo4j() -> Check:
    from graphpack.backbone.neo4j_client import connection_params

    params = connection_params()
    try:
        from graphpack.backbone import session_scope

        with session_scope() as session:
            counts = session.run(
                "MATCH (n) WHERE n.pack IS NOT NULL "
                "RETURN n.pack AS pack, count(*) AS nodes ORDER BY pack"
            )
            loaded = ", ".join(f"{r['pack']}={r['nodes']:,}" for r in counts) or "empty"
        return Check("neo4j", True, f"{params['uri']} — {loaded}")
    except Exception as exc:
        return Check(
            "neo4j", False, f"{params['uri']} — {exc}", "docker compose -f infra/compose.yaml up -d"
        )


def _check_qdrant() -> Check:
    from graphpack.packs.loader import qdrant_config

    config = qdrant_config("")
    endpoint = f"{config['host']}:{config['port']}"
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(host=config["host"], port=config["port"], timeout=TIMEOUT)
        try:
            names = [c.name for c in client.get_collections().collections]
        finally:
            client.close()
        return Check("qdrant", True, f"{endpoint} — {', '.join(names) or 'no collections'}")
    except Exception as exc:
        return Check(
            "qdrant", False, f"{endpoint} — {exc}", "docker compose -f infra/compose.yaml up -d"
        )


def _check_llm() -> Check:
    """Whether extraction has a model to call.

    Reports the provider the engine will pick, and for Ollama goes further and
    confirms the named model is actually pulled — a running server with the
    wrong model is the failure that looks like success.
    """
    provider = (os.getenv("LLM_PROVIDER") or "openai").lower()

    if provider == "ollama":
        model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
        base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        available, error = _ollama_models(base)
        if error:
            return Check("llm", False, f"ollama at {base} — {error}", "brew services start ollama")
        if not _has_model(model, available):
            return Check(
                "llm",
                False,
                f"ollama at {base} has {', '.join(available) or 'no models'}; '{model}' is not pulled",
                f"ollama pull {model}",
            )
        return Check("llm", True, f"ollama {model} at {base}")

    key_var = _API_KEY_VARS.get(provider)
    if key_var and not os.getenv(key_var):
        return Check(
            "llm",
            False,
            f"LLM_PROVIDER={provider} but {key_var} is not set",
            f"set {key_var} in .env, or LLM_PROVIDER=ollama for the local path",
        )
    model = os.getenv(f"{provider.upper()}_MODEL", "(provider default)")
    return Check("llm", True, f"{provider} {model}")


def _check_embedding() -> Check:
    """Whether chunks can be embedded.

    Separate from the LLM because they are separately configurable and commonly
    mismatched — Anthropic publishes no embedding model, so that provider always
    needs a second one alongside it.
    """
    kind = (os.getenv("EMBEDDING_KIND") or os.getenv("LLM_PROVIDER") or "openai").lower()

    if kind not in EMBEDDING_PROVIDERS:
        return Check(
            "embedding",
            False,
            f"{kind} publishes no embedding model, and the engine's embedding factory "
            "has no branch for it",
            f"set EMBEDDING_KIND to one of {', '.join(sorted(EMBEDDING_PROVIDERS))} "
            "alongside its own credentials",
        )

    if kind == "ollama":
        model = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
        base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        available, error = _ollama_models(base)
        if error:
            return Check(
                "embedding", False, f"ollama at {base} — {error}", "brew services start ollama"
            )
        if not _has_model(model, available):
            return Check(
                "embedding",
                False,
                f"ollama at {base} does not have '{model}'",
                f"ollama pull {model}",
            )
        dimension = os.getenv("EMBEDDING_DIMENSION", "768")
        return Check("embedding", True, f"ollama {model} ({dimension} dimensions)")

    key_var = _API_KEY_VARS.get(kind)
    if key_var and not os.getenv(key_var):
        return Check(
            "embedding",
            False,
            f"EMBEDDING_KIND={kind} but {key_var} is not set",
            f"set {key_var} in .env, or EMBEDDING_KIND=ollama for the local path",
        )
    model = os.getenv("EMBEDDING_MODEL", "(provider default)")
    return Check("embedding", True, f"{kind} {model}")


#: Providers the engine's embedding factory has a branch for. Anthropic, Groq
#: and OpenRouter serve no embedding model at all, so a configuration naming one
#: of them for embeddings fails at insert time, after the documents have already
#: been fetched and chunked.
EMBEDDING_PROVIDERS = frozenset(
    {
        "openai",
        "ollama",
        "google",
        "gemini",
        "azure",
        "azure_openai",
        "vertex",
        "bedrock",
        "fireworks",
        "openai_like",
        "litellm",
    }
)

_API_KEY_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
}


def _ollama_models(base_url: str) -> tuple[list[str], str]:
    """``(model names, error)`` — one of the two is always empty."""
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return [m["name"] for m in payload.get("models", [])], ""
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
        return [], f"not reachable ({exc})"


def _has_model(wanted: str, available: list[str]) -> bool:
    """Ollama reports `nomic-embed-text:latest` for a `nomic-embed-text` pull."""
    wanted = wanted if ":" in wanted else f"{wanted}:latest"
    return wanted in available
