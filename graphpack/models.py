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

#: Providers whose structured-output path rejects a schema carrying properties.
#: Separate from the set above because the two limits are unrelated: ollama
#: cannot drive the schema extractor at all, while gemini drives it well and
#: only refuses the properties.
#:
#: Google's Developer API rejects any JSON Schema containing
#: ``additionalProperties`` — which LlamaIndex emits for the ``Dict[str, Any]``
#: on an entity's properties — with "additionalProperties is only supported in
#: Gemini Enterprise Agent Platform mode". Measured on the same chunk of a
#: Turkish decision, everything else held constant:
#:
#:   with properties      ValueError, 0 triples
#:   without properties   5 triples
#:
#: The error never surfaces on its own: ``SchemaLLMPathExtractor._aextract``
#: catches ValueError and returns an empty list, so a whole ingest reports
#: success and writes nothing.
NO_STRUCTURED_PROPERTIES = frozenset({"ollama", "gemini"})

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
    plain template at construction and formats it correctly.

    Gemini refuses them for an unrelated reason — see NO_STRUCTURED_PROPERTIES.

    The cost is small either way: entity properties come from the backbone,
    which is loaded from published metadata rather than guessed from prose.
    """
    return _name(provider) not in NO_STRUCTURED_PROPERTIES


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


#: Providers whose async structured-output path is broken in LlamaIndex and has
#: to be driven synchronously from a worker thread.
#:
#: `llama_index.llms.google_genai` calls `asyncio.run(prepare_chat_params(...))`
#: inside its *synchronous* `_chat`, and the async structured-prediction path
#: reaches that method from inside a running loop — so every extraction dies on
#: "asyncio.run() cannot be called from a running event loop". The same call
#: succeeds when made with no loop on the thread, which is what the replacement
#: below arranges.
NEEDS_SYNC_EXTRACTION = frozenset({"gemini"})


def drive_extraction_synchronously(extractor, provider: str | None) -> bool:
    """Make *extractor* call its LLM from a thread with no event loop.

    Returns whether anything was changed, so a caller can log it.

    Replaces `SchemaLLMPathExtractor._aextract` on the instance's class. Two
    departures from the original, both deliberate:

    * the LLM call is the synchronous `structured_predict`, dispatched with
      `asyncio.to_thread` so the thread it runs on has no loop of its own;
    * a failed extraction is logged. LlamaIndex catches ValueError, TypeError
      and AttributeError and returns no triplets, which is why a whole ingest
      could report success after writing nothing — twice, for two unrelated
      reasons, before either was visible.
    """
    if _name(provider) not in NEEDS_SYNC_EXTRACTION:
        return False

    import asyncio

    from llama_index.core.indices.property_graph.transformations.schema_llm import (
        SchemaLLMPathExtractor,
    )
    from llama_index.core.schema import MetadataMode

    if getattr(SchemaLLMPathExtractor, "_graphpack_sync", False):
        return True

    KG_NODES_KEY, KG_RELATIONS_KEY = "nodes", "relations"

    async def _aextract(self, node):
        text = node.get_content(metadata_mode=MetadataMode.LLM)
        try:
            kg_schema = await asyncio.to_thread(
                self.llm.structured_predict,
                self.kg_schema_cls,
                self.extract_prompt,
                text=text,
                max_triplets_per_chunk=self.max_triplets_per_chunk,
            )
            triplets = self._prune_invalid_triplets(kg_schema)
        except Exception as exc:  # noqa: BLE001 — the point is that nothing is hidden
            logger.warning("Extraction failed for one chunk — %s: %s", type(exc).__name__, exc)
            triplets = []

        existing_nodes = node.metadata.pop(KG_NODES_KEY, [])
        existing_relations = node.metadata.pop(KG_RELATIONS_KEY, [])
        metadata = node.metadata.copy()
        for subject, relation, obj in triplets:
            subject.properties.update(metadata)
            obj.properties.update(metadata)
            relation.properties.update(metadata)
            existing_relations.append(relation)
            existing_nodes.append(subject)
            existing_nodes.append(obj)

        node.metadata[KG_NODES_KEY] = existing_nodes
        node.metadata[KG_RELATIONS_KEY] = existing_relations
        return node

    SchemaLLMPathExtractor._aextract = _aextract
    SchemaLLMPathExtractor._graphpack_sync = True
    return True
