# Models

Two things need a model: **embedding** every chunk, and **extracting** entities
and relations from it. Packs name neither — they describe a domain, not a
vendor — so the same pack runs against a local Ollama or a hosted API depending
only on the environment.

`graphpack doctor` reports which path is active and whether it answers.

## Local: Ollama

Free, nothing leaves the machine, and slow.

```bash
brew install ollama && brew services start ollama
ollama pull llama3.1:8b        # extraction, ~4.9 GB
ollama pull nomic-embed-text   # embeddings, ~275 MB
```

```env
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.1:8b
EMBEDDING_KIND=ollama
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIMENSION=768
```

GraphPack adjusts three things for this path, none of which the engine does and
none of which are guessable. They are in `graphpack/models.py`, with the
measurements that produced them.

**A different extractor.** `SchemaLLMPathExtractor` constrains extraction to the
ontology's types through Pydantic and is the better of the two, but it needs tool
calling that survives the round trip. The engine keeps a list of providers where
it does not, and switches those to `DynamicLLMPathExtractor` — ollama is absent
from that list even though the engine's own comment says it belongs there. Left
alone, extraction returns nothing and reports no error.

**A context window the machine can afford.** llama3.1 advertises 131,072 tokens.
The engine passes no context window, so LlamaIndex asks the model and uses that
number; Ollama then sizes its KV cache to match, taking 14.5 GB of a 16 GB
machine. Extraction chunks are about a kilobyte. Override with
`OLLAMA_CONTEXT_WINDOW` if 8192 is wrong for your model.

**Properties off.** Entities extracted on this path carry no properties — see
[ENGINE.md](ENGINE.md) for the interaction that makes them break the prompt.
Little is lost: entity properties come from the backbone, which is loaded from
published metadata rather than inferred from prose.

### What it costs in time

llama3.1:8b, M4, 16 GB: roughly 20 seconds per chunk. A thousand documents is
therefore several hours. Use `--limit` while tuning an ontology:

```bash
graphpack ingest oss --limit 20
```

`--skip-graph` runs everything except extraction, which checks documents,
chunking and embeddings in seconds rather than hours.

## Hosted API

Faster, better at schema-constrained extraction, and costs money per run.

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
EMBEDDING_KIND=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=1536
```

This path uses `SchemaLLMPathExtractor` with properties enabled — no adjustments
are applied. Anthropic publishes no embedding model, so pair it with another
provider's:

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
EMBEDDING_KIND=openai
OPENAI_API_KEY=sk-...
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=1536
```

## Changing embedding models

The vector dimension is part of a Qdrant collection's identity. Switching
models means a new collection: change `stores.qdrant_collection` in the pack, or
run `graphpack pack reset <pack>` first. Mixing dimensions fails at insert time
with a message about the wrong vector size.

## Which to use when

The development loop runs locally: it is free, and a wrong ontology is cheaper
to discover on twenty documents than on a thousand. Measured runs — the numbers
that end up in a report — should use a hosted model, and the report should say
which one produced them. The two paths do not extract identically, and a
precision figure without the model beside it means little.
