"""Provider-specific adjustments the engine does not make for itself.

The engine supports thirteen LLM providers and keeps a list of the ones whose
tool-calling breaks ``SchemaLLMPathExtractor``, switching those to the dynamic
extractor. Ollama is not on that list, although the engine's own comment says it
has the same conflict (``schema_manager.py:108``). Running it as configured
produces no entities at all, with no error.

Everything here operates on objects the engine hands back — no engine source is
modified. Each adjustment was arrived at by measuring, and each is pinned by a
test, because none of it is guessable from the documentation.

Measured on llama3.1:8b, an M4 with 16 GB, one 150-character chunk:

    as configured by the engine          140 s    0 entities
    dynamic extractor                    118 s    0 entities
    + context window 8192                 34 s    0 entities
    + properties disabled                 20 s   10 entities, 5 relations
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

#: Providers whose tool calling does not survive ``SchemaLLMPathExtractor``.
#: The engine has its own list and this one adds what measurement showed it
#: misses; ``schema_manager.py`` handles the rest.
NEEDS_DYNAMIC_EXTRACTOR = frozenset({"ollama"})

#: LlamaIndex reads llama3.1's advertised 131,072-token context and Ollama sizes
#: its KV cache to match, which on a 16 GB machine is most of the memory and
#: makes every call several times slower. Extraction chunks are ~1 KB.
DEFAULT_LOCAL_CONTEXT_WINDOW = 8192


def extractor_type_for(provider: str | None) -> str:
    """Which extractor to configure for *provider*.

    ``schema`` is the better of the two — it constrains extraction to the
    ontology's types through Pydantic rather than by asking politely — so it is
    the default, and providers that cannot drive it get ``dynamic``.
    """
    return "dynamic" if _name(provider) in NEEDS_DYNAMIC_EXTRACTOR else "schema"


def properties_supported(provider: str | None) -> bool:
    """Whether extracted entities can carry properties on this provider.

    They cannot on the dynamic path, for a reason worth writing down.
    ``DynamicLLMPathExtractor`` picks its prompt at construction: given any
    properties it takes the with-properties template. The engine then sets
    ``allowed_relation_props`` to None whenever the schema declares no relation
    properties — and LlamaIndex's ``_aextract`` only uses the with-properties
    code path when *both* property lists are non-None. So an ontology with
    entity properties but no relation properties — an entirely ordinary
    ontology, and ours — ends up formatting the with-properties template through
    the without-properties path. The model receives a prompt containing the
    literal text ``{allowed_entity_properties}`` and answers with nothing.

    Turning properties off keeps both lists unset, so LlamaIndex selects the
    plain template at construction and formats it correctly. The cost is small
    here: entity properties come from the backbone, which is loaded from
    published metadata rather than guessed from prose.
    """
    return _name(provider) not in NEEDS_DYNAMIC_EXTRACTOR


def tune_llm(llm, provider: str | None) -> None:
    """Apply local-model adjustments to an LLM instance, in place.

    Called after the engine builds it. ``HybridSearchSystem`` assigns the same
    object to LlamaIndex's global ``Settings``, so one change covers both.
    """
    if _name(provider) not in NEEDS_DYNAMIC_EXTRACTOR:
        return

    window = int(os.getenv("OLLAMA_CONTEXT_WINDOW", DEFAULT_LOCAL_CONTEXT_WINDOW))
    # LlamaIndex leaves context_window at -1 until the first call, then asks the
    # model and caches whatever it advertises. Reading it here to decide whether
    # it is "too large" therefore always sees -1 and never acts. Set it outright.
    current = getattr(llm, "context_window", -1)
    if current == -1 or current > window:
        logger.info(
            "Setting context window to %s (was %s) — llama3.1 advertises 131,072 and Ollama "
            "sizes its KV cache to match, which is most of a 16 GB machine",
            window,
            "unresolved" if current == -1 else current,
        )
        llm.context_window = window

    # DynamicLLMPathExtractor calls apredict(), which expects plain text. With
    # function calling on, achat() answers with a tool call whose content is
    # None and apredict() collapses that to the empty string — no entities, no
    # error. The engine applies this same reset, but only to the providers on
    # its own list.
    if getattr(llm, "is_function_calling_model", False):
        llm.is_function_calling_model = False
        logger.info("Disabled function calling — the dynamic extractor reads plain text")


def _name(provider) -> str:
    """Provider as a plain lowercase string, whether enum, value or None."""
    if provider is None:
        return ""
    return str(getattr(provider, "value", provider)).lower()
