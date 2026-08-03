# GraphPack: what it claims, and what it has shown

A GraphRAG pipeline is general. The thing anybody actually wants is specific:
a graph of *their* domain, with *their* identifiers, answering *their*
questions. The usual way to get from one to the other is to fork the pipeline
and edit it, and the usual result is a fork per vertical that cannot be merged
back.

GraphPack is the claim that the gap is configuration, not code — and an attempt
to hold that claim to a number rather than an argument.

## The shape

```
~/Desktop/neo4j/
├── flexible-graphrag/     the engine. Never modified. CI proves it.
└── graphpack/             this repository
    ├── graphpack/         all Python
    └── domains/           all packs — YAML, OWL, CSV, JSONL. No Python.
```

A **pack** is a directory. `ontology.ttl` says what kinds of thing exist,
`sources.yaml` says where records come from and what they become, `resolve.yaml`
says how a name in prose reaches a canonical node, `retrieval.yaml` says what
questions the graph can be asked, `eval.yaml` says how to grade the answers.
Nothing in it is executable.

Four rules, all checked in CI:

1. `graphpack/` never imports `domains/`, and `domains/` contains no `.py`.
2. No pack name appears as a string literal in `graphpack/`.
3. `git -C ../flexible-graphrag status --porcelain` is empty.
4. Every pack passes `graphpack packs validate`.

The third is the load-bearing one. "No code changes" only means something if it
has a hard boundary, and an untouched git checkout is the hardest one available.

## The claim, as a number

Three packs, chosen to share as little as possible:

| pack | domain | backbone from | resolution | language |
|---|---|---|---|---|
| `oss` | Python packaging | published metadata | exact | English |
| `tr-law` | Turkish labour case law | citations dug out of prose | fuzzy | Turkish |
| `bench-wiki` | news, as a published benchmark | bibliographic metadata | fuzzy on names | English |

`bench-wiki` was added *after* the other two were finished and measured, which
makes it the honest test. It cost:

```
297 lines of configuration     9 files, no Python
  8 lines of GraphPack code    one new pack knob: extract: false
  0 lines of engine change
```

The eight lines are the part worth dwelling on. A pack whose every edge comes
from a metadata field has nothing for a model to extract, and the contract had
no way to say so. So the contract grew a knob. That is the shape the thesis
predicts: configuration for the domain, and occasionally one general capability
that this domain happened to need first. It is not "zero code forever" — it is
"the code that gets added is general, and stays general".

## What the measurements say

Full numbers, with the commands that produce them, are in
[RESULTS.md](RESULTS.md). The two that matter here:

**The benchmark works and is well measured.** MultiHop-RAG, all 2,556 queries:
MRR@10 **0.759**, Hit@1 0.631, Hit@10 0.977, intervals ±2 points. Vector
retrieval only — the engine's BM25 leg does not survive a process boundary — and
no comparison to the published table is claimed, because matching its embedding
model and reranking is a separate run.

**Extraction is measured in two domains now, and they disagree by a lot.**
tr-law: F1 **81.4%**, precision 97.2%, over 150 gold edges, interval ±5. oss:
F1 22.2% over *twenty* gold edges, interval ±13 — a number that supports no
conclusion about the system and is not offered as one.

The gap is not the model being better at Turkish. It is three things that were
wrong in the oss run and right in the tr-law one: the backbone covered the
corpus instead of a tenth of it, the ontology was enforced during extraction
instead of ignored, and the extractor was one the provider can actually drive.
Each is in [RESULTS.md](RESULTS.md) with the measurement that showed it.

**And the graph answers what retrieval does not.** On bench-wiki, where every
article is indexed, 26.8% of a traversal's answer is recoverable from the top-30
passages — 75% when the answer is eight entities, 12% when it is fifty-one. The
byline questions recover nothing at all, because `author` is in the backbone and
in no passage. That is the case for a structured half, stated as a number rather
than asserted.

Having both in one repository is what makes either legible. Same metrics code,
same interval arithmetic, 2,255 measurements against 20: ±2 points against ±13.
Nothing about the system got more certain between those two rows.

## Three things that were learned the hard way

**Gold is scarcer than it looks.** The oss corpus grades itself: for any two
packages a thread mentions, the backbone already states whether one depends on
the other, so no annotation is needed. The catch is that both ends have to be
*in* the backbone. The backbone is the top 1,000 PyPI packages; issue threads
discuss a far wider ecosystem, and half of all package mentions resolve to
packages it does not contain. 200 documents, 3,528 entities and 391 package
mentions produced 20 scoreable pairs. An early estimate said 73. The fix is a
wider backbone, which costs nothing — backbones load without a model.

**The ontology constrains extraction only if you make it.** The oss run put
17.8% of relations and 42% of entity labels inside the ontology; the tr-law run
put 100% of both. `strict_schema: true` was set in each.

Two independent reasons the first number is what it is. On the dynamic
extractor — where Ollama has to run, because the schema extractor returns
nothing on it — no schema constrains anything, and the model invents types
freely. And on the schema extractor the engine forwards no
`kg_validation_schema` at all, so LlamaIndex validates against its own
PRODUCT / MARKET example and discards everything a real pack produces. That
second one does not degrade the graph; it empties it, silently.

So the layer earns its place twice over. It installs the pack's own constraints
on the extractor, which is what turns 17.8% into 100%. And `validate-triples`
remains the only thing in the stack that ever checks the result — without it, a
graph 82% of whose relations violate its own ontology looks exactly like one
that does not.

The 100% is by construction, not by the model being better: what does not
conform is discarded before it is written. That buys precision (97.2%) and
spends recall (70.0%). Which trade is right is now a measurable question.

**Extraction is essentially the whole cost — locally.** Two ingests on the same
machine: 200 documents *with* extraction, 10 h 37 m. 609 documents and 8,927
chunks *without*, 6 m 6 s. Chunking and embedding are free by comparison; the
model reading every chunk is the bill.

Moving that model off the laptop changes the shape entirely. The same 200
documents through a hosted model took **10 m 19 s** and cost about sixty cents.
The local run is not a cheaper version of the hosted one — it is a different
regime, and every timing in this project that predates it is a fact about a
laptop.

## The defects are the interesting part

Every one of these produced a plausible number before it produced an error.

- **`asyncio.run` per query.** It creates a loop and closes it; the engine's
  clients bind to the loop they first used. Every query after the first failed,
  and the benchmark reported a retrieval score of exactly 0.000.
- **`system.search` returns no document identity.** Its `source` field is the
  *retriever's* name, so 8,927 chunks all attributed to an article called
  "Qdrant vector". Non-empty, so the unattributed counter — written for exactly
  this — never fired. Retrieval now goes through the index, which keeps
  `ref_doc_id`.
- **Neo4j's label order chose entity types.** `__Entity__` nodes MERGE globally
  on id, so one node accumulates every type the model ever gave it. Resolution
  took `labels(e)[0]`.
- **Our own bookkeeping was extracted.** LlamaIndex prepends metadata to a
  node's text before sending it to the model, so `pack: oss` became a `PACKAGE`
  entity from a thread about botocore.
- **The extractor validated against somebody else's ontology.** The engine
  passes no `kg_validation_schema`, so LlamaIndex used its PRODUCT / MARKET
  example and `strict=True` discarded every triple a real pack produced. Turkish
  case law was being filtered against a schema about consumer products, and the
  ingest reported success.
- **`text: body`.** A template with no placeholder renders itself: 609 documents
  of four characters, which an ingest embeds without complaint.
- **`[must be empty]` on the second comment line.** The marker is read on the
  first line only, so the assertion silently became commentary and the suite
  reported OK because it had stopped looking.

The pattern is consistent enough to be a design rule: **a wrong number is worse
than an error, so every stage should be able to say which kind of failure it
had.** `graphpack bench` distinguishes "retrieved nothing" from "retrieved and
could not attribute". `graphpack eval` distinguishes "nothing ingested" from
"nothing resolved" from "nothing co-mentioned". Both distinctions exist because
the undifferentiated version sent somebody to the wrong place.

## Where it is weak

Stated plainly, because the numbers above are only worth what the caveats
allow.

- **The two domains are not a controlled comparison.** oss ran on a local model
  with the dynamic extractor; tr-law on a hosted one with the schema extractor.
  Domain, language, model and extractor all differ at once, so the two F1
  figures cannot be attributed to any one of them. Re-running oss on tr-law's
  setup would settle it and costs about a dollar.
- **The oss measurement is weak on its own terms.** ±13 points on twenty edges.
- **Article-level citations score nothing.** The extracted mention is `"371.
  maddesinde"` and the backbone identifier is `madde:6100/371`: an article
  number alone identifies nothing, and the statute was in the sentence
  extraction discarded. Resolving it needs context-dependent resolution, which
  does not exist yet.
- **The benchmark is vector-only**, and unreranked.
- **The ablation covers one pack.** `graphpack ablate bench-wiki` measures how
  much of a graph answer is recoverable from text alone — 26.8% at top-30, and
  falling as the answer set grows (75% at eight entities, 12% at fifty-one).
  The same command on tr-law is confounded by its corpus being a 200-document
  sample of a 1,578-decision graph, so that number is reported and not used.
  What none of it measures is whether an end-to-end system *answers* the
  question; name presence is a lower bound, not an answer.
- **One machine, one model.** M4, 16 GB, llama3.1:8b. Every timing and much of
  the extraction quality is a fact about that, not about the design.

## What is actually reusable

If the thesis is right, the transferable parts are not the packs:

- **Self-labelling evaluation.** A corpus that already contains its own ground
  truth — dependency metadata, citation text, a benchmark's evidence list — can
  be scored without annotation. The generator is per-domain; the bargain is not.
- **Post-hoc resolution.** Linking extracted mentions to canonical nodes as a
  separate Cypher pass, rather than inside ingest, means rules can change and be
  re-run in seconds instead of hours. On a 10-hour ingest that is the difference
  between iterating and not.
- **Enforcing the ontology yourself.** The engine accepts a schema and forwards
  no constraints, and the library underneath then validates against its own
  example schema — so extraction either ignores the ontology or returns nothing,
  depending on the extractor, and neither raises. Anybody building on a similar
  stack is in the same position and probably does not know it. One query catches
  it: are the extracted relation types the ones the ontology declares?
- **Intervals on every score.** Small sets produce confident-looking numbers.
  The interval is what stopped 22.2% from being reported as a result.
