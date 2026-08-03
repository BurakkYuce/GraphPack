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

**Triple constraints are not in the schema the engine is given.**
`SchemaManager` passes `possible_entities`, `possible_relations` and the
property lists to `SchemaLLMPathExtractor` and never `validation_schema`, so a
pack's `rdfs:domain`/`rdfs:range` are absent from what the engine builds.

What that *means* depends on the extractor, and the two answers are opposite —
see "Triple constraints govern nothing on the dynamic path and everything on
the schema path" below. GraphPack installs the constraints itself and also
checks them in its own pass.
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

**A second ingest into a pack that already holds data fails, and the first one
works.** This is a defect in `llama-index-graph-stores-neo4j` rather than in the
engine, and it is worth knowing because the failure mode hides behind a habit:
every ingest this project ran was preceded by `pack reset --extraction-only`, so
it went unseen until per-document re-ingest needed it not to be.

Inserting nodes refreshes the store's schema, and the enhanced-schema path
samples distinct values for indexed string properties:

```python
# neo4j_property_graph.py, _enhanced_schema_cypher
distinct_values = self.query(
    f"CALL apoc.schema.properties.distinct('{label}', '{prop}') YIELD value"
)[0]["value"]
```

`Neo4jPropertyGraphStore` has no `query`. It has `structured_query`, with a
compatible signature. The branch is reached only when a RANGE index has
`size > 0` — an index *with data in it* — which is exactly why an empty graph
survives and a populated one raises `AttributeError` and writes nothing.

`graphpack/models.py` installs the missing method on the class, the same way it
replaces `SchemaLLMPathExtractor._aextract`.
→ `allow_schema_refresh_on_a_populated_graph`

**`PropertyGraphIndex.delete_ref_doc` deletes nothing across a process
boundary.** It works through the index's in-memory docstore, which is built by
whichever process ingested; a process that opens an existing graph has an empty
one. The call then returns cleanly and removes no chunks — verified against a
document with one chunk, whose count was unchanged afterwards.

Same shape as the BM25 leg, and the same cause: the engine keeps in a process
what the database could hold. `graphpack/reingest.py` deletes chunks with its
own Cypher on `ref_doc_id`. The vector store's `delete` does work across
processes and is used as-is.

**Neo4j is Community edition**, which supports exactly one database. Packs share
`neo4j` and are separated by a `pack` property on every node.

**Every RANGE index is label-scoped, and a graph keyed on identifiers cannot
always name a label.** This is Neo4j's rather than the engine's, and it cost
this project half an hour per load before anyone looked.

An edge names its endpoints by identifier. A pack's identifiers span every label
it declares — `pypi:requests` is a `Package`, `gh:psf/requests` a `Repository` —
so the endpoint match was written without one:

```cypher
MATCH (a {pack: $pack, id: row.start})
```

That match can use no index at all, so it scans every node in the database once
per batch. Measured on the oss backbone: batches climbing from 15 seconds to
nearly two minutes as the graph filled, and **thirty minutes to load 92,023
rows**.

The fix is one shared label. Every loaded node now carries `:Thing` alongside its
own, the endpoint match names it, and there is a `(pack, id)` index on it:

```
  30 minutes  ->  5 seconds        (oss, 92,023 rows, identical counts)
```

An index rather than a constraint, deliberately: uniqueness is already enforced
per label, and imposing it across labels would invent a new way for a load to
fail. Two labels in one pack sharing an identifier is unusual but not incoherent.

**Triple constraints govern nothing on the dynamic path and everything on the
schema path — and the engine forwards nobody's.** This corrects an earlier
version of this note, which said they never reach the extractor at all.

`SchemaManager.create_extractor` builds `SchemaLLMPathExtractor` with
`possible_entities` and `possible_relations` and never passes
`kg_validation_schema`. LlamaIndex then falls back to
`DEFAULT_VALIDATION_SCHEMA` — its PRODUCT / MARKET / TECHNOLOGY example — and
with `strict=True` discards every triple whose types are not in it
(`schema_llm.py:317`). A pack's triples never are, so extraction returns
nothing at all, and `_aextract` catches the failure and reports an empty list.
GraphPack installs the pack's own constraints after construction.

On the **dynamic** path the note's original claim holds: nothing constrains
extraction, and `strict_schema: true` is inert. Ollama must run there — the
schema extractor returns zero entities on it — so on a local model 58% of the
entity labels written are types the ontology never declared: `URL`, `FUNCTION`,
`FILE`, `DATE`, `CLASS`, and 17.8% of relations conform.

On the **schema** path, with the constraints installed, conformance is 100% by
construction: what does not conform is discarded rather than written. Both
numbers are in [RESULTS.md](RESULTS.md).

**Google's Developer API rejects a schema carrying properties.** LlamaIndex
emits `additionalProperties` for an entity's property dict; the API refuses it
with a message about Enterprise Agent Platform mode. Measured on one chunk with
everything else held constant: with properties, `ValueError` and 0 triples;
without, 5. So "can this provider carry properties" and "can it drive the schema
extractor" are separate questions — `graphpack/models.py` keeps two sets.

**`llama_index.llms.google_genai` calls `asyncio.run` inside its synchronous
`_chat`**, and the async structured-output path reaches that method from inside
a running loop, so every extraction dies on "asyncio.run() cannot be called from
a running event loop". GraphPack dispatches the call with `asyncio.to_thread`,
onto a thread with no loop of its own.

**`SchemaLLMPathExtractor._aextract` swallows the errors that cause all of the
above.** It catches `ValueError`, `TypeError` and `AttributeError` and returns
no triplets, so an ingest reports success and writes nothing. Two unrelated
faults hid behind that for a full run each. GraphPack's replacement logs what
it caught.

**Node metadata is part of the prompt.** LlamaIndex prepends every metadata key
to a node's text as a `key: value` line before sending it to a model, so a field
kept for bookkeeping is a field the extractor reads as document content. This
produced an entity called `pack: oss`, typed `PACKAGE`, out of a thread about
botocore — confirmed by the fact that no chunk's `text` contains the string
while `MetadataMode.LLM` renders it. GraphPack excludes the pack tag from both
the LLM and embedding views, and `hide_from_model` lets a pack exclude more.
→ `tests/test_corpus.py`

Measured on the oss corpus: with `url` visible, **31.4% of extracted entities
were named by a URL** — 154 of 490 — and every one carried a type the ontology
declares (`ISSUE`, `PACKAGE`, `REPOSITORY`), so `validate-triples` reported 100%
conformance throughout. Conformance checking cannot see this class of error.
Hiding the field took it to 0.7%, and the structure it had crowded out came
through: repository edges 10 → 85. See [RESULTS.md](RESULTS.md).

**Metadata is also subtracted from the chunk-size budget**, which is a second
reason to hide what is not content:

```python
# llama_index.core.node_parser SentenceSplitter.split_text_metadata_aware
metadata_len = len(self._tokenizer(metadata_str))
effective_chunk_size = self.chunk_size - metadata_len
```

So every metadata key shortens the text each chunk can hold. Hiding three fields
from the oss corpus took it from 604 chunks to 518 — 14% fewer, which is 14%
fewer model calls for the same documents. It also means a pack with verbose
metadata and a small `chunk_size` can raise `ValueError` outright, which the
splitter does deliberately rather than producing empty chunks.

**The graph half of an ingest is all-or-nothing; the vector half is not.** The
two land at different times, and it matters because only one of them survives an
interruption.

Measured at 515 of 611 chunks extracted, 9 hours into a run:

```cypher
MATCH (c:Chunk) RETURN count(c)        // 0
MATCH (e:__Entity__) RETURN count(e)   // 0
```
```
GET /collections/oss_chunks  ->  points_count: 1672   (steady over 150 s)
```

So chunking and embedding complete first and write as they go; extraction then
runs over every chunk and the property-graph upsert happens after all of it. An
earlier version of this note said "extraction writes nothing until it finishes"
and left the vector store out — measured at 310/611, when Neo4j was empty, that
read as the whole pipeline being all-or-nothing. It is not.

Three consequences, none worked around:

- **A crash near the end costs the graph, not the embeddings.** Nine hours of
  extraction is lost; the vectors are already in Qdrant and a re-run re-embeds
  them for nothing but does not have to be avoided. Nothing here checkpoints,
  and adding it would mean changing the engine. The mitigation available to a
  pack is a smaller `--sample`: several short runs survive an interruption that
  one long run does not, and `--seed` keeps the selections comparable.
- **Progress cannot be measured from the graph.** Counting nodes mid-ingest
  reports zero and means nothing.
- **Nor from the vector store**, once embedding has finished — the count goes
  steady while hours of extraction remain. The live meter is the model server's
  own request log; on Ollama, one `/api/chat` completion is one chunk, which was
  checked against the extraction counter (309 completions at the moment it
  reported 310).

**The property-graph write is its own phase, and it is not small.** Measured on
the full tr-law corpus — 1,578 documents, 4,689 chunks, a hosted extraction
model — the engine's own breakdown was:

```
Direct document processing completed in 3301.39s —
  Chunk: 420.27s   Vector: 2.59s   Search: 1.40s
  KG: 1506.14s     Graph: 1367.67s
```

So **the graph write took 23 minutes against extraction's 25**, and it produced
nothing observable while it ran: Neo4j held zero chunks and zero entities for
the whole 23 minutes, and no query was executing for most of it. The work is
elsewhere — the store embeds every extracted entity's name for its
`__Entity__.embedding` vector index, one request at a time. On the 200-document
oss run the same phase took 66 seconds for 419 entities, which is 0.157s each
and predicts 70 minutes for tr-law's 26,914 before deduplication.

Two consequences for anyone planning a large ingest. The wall clock is roughly
*double* the extraction estimate, not equal to it. And moving extraction to a
hosted model does not move this: it is local embedding, so it stays on the
laptop however fast the extractor gets.

GraphPack's own code never queries that entity vector index. The engine's
property-graph retriever, which is one leg of `system.hybrid_retriever`, may —
so it is not dead weight, and no knob is offered to skip it.

## Updating the pin

Bumping `ENGINE_REF` in `.github/workflows/ci.yml` (and the note in
`pyproject.toml`) is a deliberate act: measured results are only comparable
within one engine version. Re-run the evaluation afterwards and say which
version produced which numbers.
