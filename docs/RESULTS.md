# Measured results

Every number here comes from a run that can be repeated with the commands
beside it. Where a number is too small to support a conclusion, it says so
instead of being rounded into confidence.

Engine pinned at `71ce503`. Hardware: M4, 16 GB. Models: `llama3.1:8b` for
extraction, `nomic-embed-text` for embeddings, both local through Ollama.

## The oss corpus, phase 4

```bash
uv run graphpack ingest oss --sample 200 --seed 0
uv run graphpack resolve oss
uv run graphpack eval oss
```

### What the ingest cost

| | |
|---|---|
| documents | 200 (sampled from 3,413, seed 0) |
| chunks | 611 |
| wall clock | 10 h 37 m |
| rate | ~52 chunks/hour, one `/api/chat` per chunk |
| entities extracted | 3,528 |
| `MENTIONS` edges | 6,491 |

### The headline, and why it is not a headline

```
dependencies (backbone_edges: DEPENDS_ON)
  P 17.6% [8.3–33.5]   R 30.0% [14.5–51.9]   F1 22.2%
  tp 6, fp 28, fn 14 — 20 gold edges, 17 of 147 documents carried gold
```

Twenty gold edges. Precision could be anywhere from 8% to 34% and the data
cannot distinguish those. The right reading is not "the system scores 22%" but
"this corpus slice cannot measure the system to better than ±13 points".

That is a finding about the evaluation design, not an excuse. The next section
is why the number of gold edges is so small, because that is the thing worth
fixing.

### Where the evaluable signal goes

Gold requires one document to mention two packages that the backbone already
relates. The funnel from 200 documents to 20 such pairs:

| stage | count |
|---|---:|
| entities extracted | 3,528 |
| …typed `PACKAGE` | 391 |
| …resolved | 372 (95%) |
| …to a backbone `Package` | 151 |
| …to a `Provisional` package | 197 |
| distinct backbone packages reached | 98 |
| documents with ≥1 backbone package | 147 |
| documents with ≥2 | 39 |
| **pairs both co-mentioned and related in the backbone** | **20** |

The dominant loss is the fourth row: **half of the package mentions are packages
the backbone does not contain.** The backbone is the top 1,000 PyPI packages;
issue threads from 120 repositories discuss a much wider ecosystem. Resolution
is working — 95% of package mentions reach *something* — but half reach a
provisional node, and a provisional node cannot carry gold because the backbone
states no dependency for it.

An earlier estimate in this project predicted ~73 gold edges from 200 documents.
It was wrong by a factor of three, because it assumed mentions would land in the
top-1000 at roughly the rate they appear. They land at half that.

The cheapest fix is not a bigger sample but a **wider backbone**: the same 200
documents against a top-10,000 backbone would convert far more of those 197
provisional resolutions into gold-bearing ones, at no extraction cost, because
the backbone is loaded without a model.

### Where the misses come from

Of ten sampled misses, eight are the same shape:

```
no relation extracted: pypi:requests -> pypi:certifi:
  both ends resolved, nothing between them
  (seen together in gh:psf/requests#5797)
```

The model named both packages and stated no relation between them. Two more were
"related, but as another type" — the relation was found and labelled something
the ontology does not pair that way, e.g. `ipython -> psutil` extracted as
`USED_BY`.

So recall is lost at relation extraction, not at entity extraction or
resolution. That is worth knowing before anyone tunes an alias table.

## The ontology does not constrain extraction. At all.

```bash
uv run graphpack validate-triples oss
```

| verdict | relations | share |
|---|---:|---:|
| conforming | 1,033 | 17.8% |
| wrong types | 2,442 | 42.1% |
| undeclared relation | 2,328 | 40.1% |

And at the entity level, 3,918 labels were written of which **1,644 (42%) are
types the ontology declares**. The rest are types the model invented: `URL`
(114), `FUNCTION` (111), `FEATURE` (105), `FILE` (89), `DATE` (66), `CLASS` (64).

`pack.yaml` sets `strict_schema: true`. It changes nothing here, and the reason
is a chain of two engine facts already documented in [ENGINE.md](ENGINE.md):

1. The engine never forwards triple constraints to the extractor, so
   `rdfs:domain` / `rdfs:range` govern nothing during extraction.
2. Ollama has to run on `DynamicLLMPathExtractor` — on the schema extractor it
   returns zero entities — and the dynamic extractor invents types by design.

So on a local model the ontology is documentation, and GraphPack's
`validate-triples` pass is the only thing that ever checks it. That pass is the
concrete value of the layer: without it, a graph 82% of whose relations violate
its own schema looks exactly like one that does not.

**A discrepancy worth naming.** An early chunk-size sweep in this project
measured "93% ontology conformance" on a probe. That figure was taken on a
handful of chunks and does not survive contact with the full corpus at
triple level. The number to trust is the one above, over 5,803 relations.

## Two defects the analysis found

**Our own bookkeeping was being extracted.** The model produced an entity named
`pack: oss`, typed `PACKAGE`, from a thread about botocore. No chunk's text
contains the string — LlamaIndex prepends metadata to a node as `key: value`
lines before sending it to the model, so the extractor was reading GraphPack's
pack tag as document content. Fixed: the tag is excluded from what the model and
the embedder see, and a pack can now mark more of its own metadata the same way
(`hide_from_model`). **The numbers above were measured before this fix**, on a
run that included the contamination.

The same mechanism means the model also reads `url:`, `state: closed` and
`created_at:` as document text, which is where much of the `URL` and `DATE`
entity noise comes from. The oss pack's declared metadata was deliberately left
unchanged, so the configuration in the repository is the one that produced these
numbers; changing it is a decision for the next run to measure.

**Neo4j's label order was deciding entity types.** The store MERGEs
`__Entity__` nodes globally on id, so one node accumulates every type the model
ever assigned it — `requests` was labelled both `REPOSITORY` and `PACKAGE`.
Resolution took `labels(e)[0]`, which is Neo4j's ordering rather than any
decision. Now every applicable rule is tried and the strongest match wins, with
the pack's declaration order as the tie-break.

| | before | after |
|---|---|---|
| mentions resolved | 471 / 781 | 490 / 781 |
| exact matches | 244 | 254 |
| documents carrying gold | 13 of 99 | 17 of 147 |
| F1 | 20.0% | 22.2% |

The F1 movement is inside the interval and should not be read as an
improvement. The structural change — 99 to 147 documents with a resolved
backbone entity — is the real one.

**A false lead, recorded because it cost an hour.** The resolve log reported
"Indexed 768 Repository nodes (0 distinct match forms)", which reads as a broken
index. It is not: `exact` and `alias` match against the identifier set, which is
always built, and only `fuzzy` needs the names. `REPOSITORY` and `RELEASE` do
not ask for fuzzy, so having no names is correct. The log line now says which
kind of matching a rule does instead of reporting an absence.

## The MultiHop-RAG benchmark, phase 7

```bash
uv run graphpack backbone load bench-wiki    # 609 articles, no model
uv run graphpack ingest bench-wiki           # 6 m 6 s, no extraction
uv run graphpack bench bench-wiki            # all 2,556 queries
```

| metric | value | 95% interval |
|---|---:|---|
| Hit@1 | 0.631 | 0.610 – 0.650 |
| Hit@2 | 0.794 | 0.777 – 0.810 |
| Hit@4 | 0.909 | 0.896 – 0.920 |
| Hit@10 | 0.977 | 0.970 – 0.982 |
| MRR@10 | **0.759** | |

2,255 answerable queries; 301 with no answer in the corpus.

The contrast with the oss evaluation is the point of having both. Same
machinery, same intervals, 2,255 measurements instead of 20: ±2 points instead
of ±13. Nothing about the system got more certain — the measurement did.

### What this is a number for, exactly

- **Vector retrieval only.** The engine's BM25 leg is an in-memory docstore
  belonging to the process that ingested, so a benchmark run as a separate
  command has no full-text half. `LI BM25: get_retriever() returned None` is the
  engine saying so. A true hybrid number needs ingest and benchmark in one
  process, which is a run this has not done.
- `nomic-embed-text`, chunk size 1024, overlap 128, top-30 retrieved.
- **Chunks are reduced to articles.** Several chunks of one article are one
  result, taking the rank of its best chunk. Counting chunks would make Hit@10 a
  measure of how finely an article was split.
- **No comparison to the published table is made here.** The paper's numbers
  depend on its embedding model, chunk size and whether a reranker ran; quoting
  ours beside theirs without matching that would be a comparison in appearance
  only. What the pack establishes is that the comparison is now one run away.

### The null queries measure nothing, and that is worth saying

301 queries have no answer in the corpus. Zero of them retrieved nothing —
which is not a failure, because a vector retriever has no way to abstain. It
returns its top-k for any input, so "retrieved nothing" can never happen and the
metric is inert.

It is kept, reported separately, and labelled rather than dropped, because the
number that cannot move is itself the finding: answering "there is nothing here"
is a decision the retrieval layer cannot make, and has to be made above it.

### Extraction is 97% of the cost

Two ingests, same machine, same models:

| pack | documents | chunks | extraction | wall clock |
|---|---:|---:|---|---:|
| oss | 200 | 611 | yes | 10 h 37 m |
| bench-wiki | 609 | 8,927 | no | 6 m 6 s |

Three times the documents and fifteen times the chunks, in one hundredth of the
time. Chunking and embedding are not what makes a GraphRAG ingest expensive on
local hardware; the model reading every chunk is. That is the number to put
beside `extract: false` when deciding whether a pack needs it.

## What has not been measured

- **tr-law extraction.** Its corpus has never been ingested — 0 `__Entity__`
  nodes — so the cross-pack comparison of resolution methods is not available.
  Everything reported for tr-law so far is its backbone and its traversals.
- **Hybrid retrieval.** Every benchmark number above is the vector leg alone.
- **The published MultiHop-RAG table.** See above: matching its setup is a
  separate run, not a paragraph.
- **`strict_schema` true versus false.** The sweep this phase planned is not
  worth running: on the dynamic extractor the setting is inert, and the section
  above is the evidence.
- **The holdout.** `eval.yaml` sets `holdout: 0.0` with a note to raise it
  before error analysis feeds back into `aliases.csv` or a normaliser. Nothing
  in this phase did: both fixes were defects — a contaminated prompt and an
  arbitrary label choice — and neither was derived from which gold edges were
  missed. The holdout stays at 0 and the note stands for the first change that
  does chase a miss.
