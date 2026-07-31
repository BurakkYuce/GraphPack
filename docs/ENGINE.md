# Working against the engine

GraphPack builds on [`stevereiner/flexible-graphrag`](https://github.com/stevereiner/flexible-graphrag),
pinned at commit **`71ce503837475c02cfcfb80cd882e3721fcbe1bc`**. The engine
repository is a read-only sibling
checkout. Nothing in GraphPack writes to it, and CI fails if anything does.

```
~/Desktop/neo4j/
├── flexible-graphrag/     upstream clone, never modified
└── graphpack/             this repository
```

## Why zero changes

The project's claim is that adding a vertical costs configuration and no code.
That is only checkable if "no code" has a hard boundary, and `git status
--porcelain` on the engine checkout is the hardest boundary available. It also
keeps upstream syncing free.

The proof is a CI step, not a promise. Note that it relies on the engine's own
`.gitignore` to hide build artifacts: an editable install leaves
`flexible_graphrag.egg-info/` and `__pycache__/` in the tree, both of which the
engine already ignores. Modifications to *tracked* files, and any new untracked
file, still fail the check.

## What GraphPack uses, and how

| Engine surface | Where | Used for |
|---|---|---|
| `rdf.ontology_manager.OntologyManager` | `graphpack/packs/ontology.py` | parse `ontology.ttl` into entity/relation/property structures |
| `config.Settings` | `graphpack/packs/loader.py` | one pack becomes one configured engine |
| `hybrid_system.HybridSearchSystem.from_settings` | `graphpack/packs/loader.py` | instantiate the pipeline |
| `ingest.ingest_from_source.ingest_source_documents` | phase 2 | feed pre-built `Document` lists through chunk → embed → index → extract |
| `query_engine.search`, `system.hybrid_retriever` | phase 6 | agent tools |

Everything else — backbone loading, resolution, evaluation, migrations — talks
to Neo4j and Qdrant directly.

## Engine behaviours that shaped the design

These are load-bearing. Each one is asserted by a test so an upstream change
surfaces as a failure rather than as wrong numbers.

**Flat top-level modules.** The engine installs `config`, `main`, `backend`,
`hybrid_system`, `ingest`, `process`, `sources`, `rdf` and more as top-level
names (`pyproject.toml` `py-modules` + `packages.find`). Every line of GraphPack
therefore lives under the single `graphpack/` package; a top-level `config.py`
of ours would shadow the engine's.
→ `tests/test_constitution.py::test_no_top_level_module_shadows_the_engine`

**`USE_ONTOLOGY` is process-global.** That path reads
`rdf.api_rdf_enhancements.ontology_manager`, a module-level singleton, so it
cannot represent two packs. GraphPack compiles the ontology into a named
`schemas` entry instead, which lives on one `Settings` instance.
→ `tests/test_loader.py::test_ontology_singleton_path_stays_off`

**Triple constraints never reach the extractor.** `SchemaManager` passes
`possible_entities`, `possible_relations` and the property lists to
`SchemaLLMPathExtractor` — never `validation_schema`. The only consumer is the
Ladybug adapter. So `rdfs:domain`/`rdfs:range` in a pack ontology do *not*
constrain extraction; GraphPack derives the constraints and enforces them itself
in the resolution pass.
→ `tests/test_ontology_compiler.py::test_triple_constraints_are_derived_but_kept_out_of_the_engine_schema`

**Reserved schema names.** `Settings.get_active_schema` short-circuits on
`sample` (returns the engine's built-in `SAMPLE_SCHEMA`), and on `default`,
`none` and the empty string (falls back to LlamaIndex's internal schema) before
it ever looks at the `schemas` list. A pack with one of those names would be
ingested under a schema unrelated to its ontology, silently. The contract
rejects them.
→ `tests/test_pack_contract.py::test_engine_reserved_names_are_rejected`

**`{TYPE}_VECTOR_DB_CONFIG` outranks programmatic config.** `Settings.__init__`
reads it *after* `super().__init__`, unconditionally, overwriting whatever was
passed in. Combined with `.env` being read relative to the working directory, a
stray shell variable can redirect an entire ingest. `clear_engine_env()` removes
these and logs what it removed.
→ `tests/test_loader.py::test_named_store_env_var_cannot_override_the_pack`

**Ontology names arrive upper-cased**, and only explicit `a owl:Class` /
`a owl:ObjectProperty` declarations are seen — `rdfs:Class` alone yields nothing.
A relation without `rdfs:range` is paired with *every* entity type, which is why
`packs validate` rejects it.
→ `tests/test_ontology_compiler.py`

**BM25 is in-memory only.** `LlamaIndexBM25SearchAdapter` holds a
`SimpleDocumentStore`; its `persist_dir` option is marked "future use". The
full-text leg of hybrid search exists only inside the process that ingested. From
phase 2 onward, ingest and query therefore share one long-lived process.

**Ollama is missing from the incompatible-provider list.** `SchemaManager`
switches bedrock, fireworks, groq, openai_like, openrouter and vllm to
`DynamicLLMPathExtractor` because their tool calling does not survive
`SchemaLLMPathExtractor`. Ollama is not on that list, although the comment at
`schema_manager.py:108` says it has "the same tool_choice conflict as direct
Ollama". Run as configured, extraction returns zero entities and raises nothing.
`graphpack/models.py` adds ollama to the switch on GraphPack's side.

**Properties break the dynamic path for ordinary ontologies.**
`DynamicLLMPathExtractor` selects its prompt at construction: given any property
list it binds the with-properties template. The engine then sets
`allowed_relation_props` to None whenever the schema declares no relation
properties — and LlamaIndex's `_aextract` only takes the with-properties code
path when *both* lists are non-None. An ontology with entity properties and no
relation properties therefore formats the with-properties template through the
without-properties path, and the model receives a prompt containing the literal
text `{allowed_entity_properties}`. GraphPack sets `disable_properties=True` for
providers on the dynamic path, which keeps both lists unset so LlamaIndex picks
the plain template.

**The Ollama context window is never set.** The engine constructs
`Ollama(model, base_url, temperature, request_timeout)` and passes no context
window, so LlamaIndex leaves it at -1 until the first call, asks the model, and
caches whatever it advertises — 131,072 for llama3.1. Ollama sizes its KV cache
to match, which is 14.5 GB of a 16 GB machine. `tune_llm` sets it outright
rather than conditionally, because at construction time the value is still
unresolved and any "is it too large" test sees -1.

Measured together, on llama3.1:8b, M4, 16 GB, one 150-character chunk:

| configuration | time | result |
|---|---:|---|
| as the engine configures it | 140 s | 0 entities |
| dynamic extractor | 118 s | 0 entities |
| + context window 8192 | 34 s | 0 entities |
| + properties disabled | 20 s | 10 entities, 5 relations |

**Neo4j is Community edition**, which supports exactly one database. Packs share
`neo4j` and are separated by a `pack` property on every node.

## Updating the pin

Bumping `ENGINE_REF` in `.github/workflows/ci.yml` (and the note in
`pyproject.toml`) is a deliberate act: measured results are only comparable
within one engine version. Re-run the evaluation afterwards and say which
version produced which numbers.
