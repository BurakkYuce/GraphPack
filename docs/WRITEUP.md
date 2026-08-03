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

**The benchmark works and is well measured.** MultiHop-RAG, all 2,556 queries,
vector retrieval: MRR@10 **0.759**, Hit@1 0.631, Hit@10 0.977, intervals ±2
points.

**And the full-text half finally has a number.** Every figure this project had
published was the vector leg alone — not by choice, but because the engine's
BM25 docstore lives in memory on the object that ingested, so a benchmark run as
a separate command has vectors and nothing else. Doing both in one process
(`bench --ingest --hybrid`) scores the fusion retriever: MRR@10 **0.777**, Hit@4
0.909 → **0.953**. Hit@1 does not move at all. Fusion is not finding a better
first answer; it is pulling more of the right articles into positions two
through four — which for a multi-hop benchmark, where an answer rests on
several articles, is the half of the ranking that matters and the half a single
number hides.

**That 0.759 is not comparable to the paper's 0.586, and this repository said
otherwise twice.** Both earlier versions claimed the gap was one embedding model
and a reranker away. Reading the paper shows the metrics differ: it scores
*chunks* where we reduce chunks to articles and score those, and its Hit@K is
recall over a query's whole evidence set where ours asks whether any one piece
was found. Both differences make our task easier, which is the only reasonable
reading of a small open embedding model with no reranker beating the published
best by 30%. RESULTS.md carries the table and what a real comparison would take.

**Extraction is measured in two domains now.** tr-law: F1 **81.4%**, precision
97.2%, over 150 gold edges, interval ±5. oss took longer to become measurable at
all, and the reason it eventually was is the more useful half of the story —
see the next section.

That gap looked like it had three causes — a better model, an enforced
ontology, a working extractor — so oss was re-run on tr-law's exact setup to
find out. **Two of the three did nothing.** Precision came back identical to
the decimal, recall halved, and F1 went from 22.2% to 15.4%: inside the same
interval, and certainly not an improvement.

What was left is the pack — and specifically, whether its documents are nodes.
tr-law's are: a decision cites a statute, so every citation is a scoreable fact
about that document, and all 88 documents with a resolved entity carried gold.
oss's documents were not in its graph, so gold had to come from two related
packages happening to appear in the same thread. 69% of its threads mention one
package. Widening the backbone eight-fold was tried and bought two gold edges.

**Then the diagnosis was acted on, and this one held.** Three load steps giving
each thread an `Issue` node and an edge to the package its repository publishes,
one eval task using the document-shaped generator: **24 gold edges to 135, and
the interval from ±13 points to ±6.** No GraphPack code, no engine change, and
no re-extraction — it scores the same run, so the gold generator is the only
variable. Recall 85.2%, precision 52.8%.

Two things that matters for, beyond oss finally being measurable. It is the
third diagnosis this project wrote down and ran, and the first that survived —
which is the point of writing them down. And it is the thesis in miniature: what
made a domain measurable was configuration.

Read the precision with its ceiling, though. Gold holds one package per thread
while extraction resolves every package the thread discusses — 135 possible gold
pairs against 218 claimed — so precision cannot exceed **61.9%** however good the
model is, and most of the excess is correct reading that this gold cannot
credit. `graphpack eval` prints that cap whenever it binds, because a capped
precision read as an error rate is the kind of wrong number this project treats
as worse than an error. The new task also asks something easier than the old
one, and both are kept side by side so that is visible rather than buried.

The extractor change was not wasted: both packs now extract 100% conforming
relations and zero invented entity types, where the local run managed 17.8% and
42%. It buys a graph that means what its ontology says. It does not buy score.

**And the graph answers what retrieval does not.** On bench-wiki, where every
article is indexed, 26.8% of a traversal's answer is recoverable from the top-30
passages — 75% when the answer is eight entities, 12% when it is fifty-one. The
byline questions recover nothing at all, because `author` is in the backbone and
in no passage. That is the case for a structured half, stated as a number rather
than asserted.

Having both in one repository is what makes either legible. Same metrics code,
same interval arithmetic, 2,255 measurements against 24: ±2 points against ±13.
Nothing about the system got more certain between those two rows — and the way
the second row eventually narrowed to ±6 was by changing what counted as gold,
not by changing the system at all.

## Three things that were learned the hard way

**Three diagnoses, written down here in order. The first two were wrong.** The
tr-law/oss gap was attributed to model, ontology enforcement and extractor; a
controlled re-run showed the first two changed the graph's quality and not its
score. What was left — oss's backbone covering a tenth of what its corpus
discusses — was then written down as the remaining cause, and widening that
backbone eight-fold moved the score by two gold edges.

The actual cause was structural and neither guess came near it: 69% of oss's
documents mention exactly one package, and its gold generator needs two. That
was the third diagnosis, written into this file before it was run, and running
it took the gold set from 24 edges to 135.

The lesson is not that the first two guesses were bad. It is that writing an
attribution down is what makes it a claim somebody can run, and the running is
cheap — a dollar and twenty minutes, twice — while the wrong belief would have
shaped every decision after it. The third one was cheaper still: no model ran at
all, because the change was configuration.

**Gold is scarcer than it looks, and the generator decides how scarce.** The oss
corpus grades itself: for any two packages a thread mentions, the backbone
already states whether one depends on the other, so no annotation is needed. The
catch is that a *pair* has to be in one document. 200 documents, 3,528 entities
and 391 package mentions produced 20 scoreable pairs. An early estimate said 73.
Widening the backbone eight times over — the obvious fix, and free, because
backbones load without a model — produced 24.

The same 200 documents, the same extraction run, scored by a generator that asks
about one document rather than a pair: **135.** Nothing about the corpus changed.
What a corpus can grade depends on the question you ask it, and that is a design
decision made in `eval.yaml` rather than a property of the data.

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
- **Our own bookkeeping was extracted, and it was a third of the graph.**
  LlamaIndex prepends metadata to a node's text before sending it to the model,
  so `pack: oss` became a `PACKAGE` entity from a thread about botocore. Once
  that was known, the scale of it was still not: **31.4% of oss's extracted
  entities were named by a URL** — the `url:` line we had added — and every one
  was typed `ISSUE`, `PACKAGE` or `REPOSITORY`, so `validate-triples` reported
  100% conforming the whole time. Hiding the field took it to 0.7%, and the
  structure that had been crowded out came through: repository edges went from
  10 to 85 and the dependency task's F1 from 14.6% to 21.1%.
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

- **oss's dependency task still cannot be measured.** Twenty-four gold edges,
  ±13 points, and widening the backbone eight-fold moved that by two. That task
  is reported and no conclusion is drawn from it. What changed is that the pack
  now also carries a task that *can* be measured — 135 edges, ±6 — so the domain
  is no longer unmeasurable even though the harder question about it is.
- **And that measurable task asks an easier question, and leaks a little.** "Is
  the thread's own package named in the thread" is easier than "did the model
  find a dependency relation", and its precision is capped by how the gold is
  built. Its gold also comes from the repository slug, which the model can see
  as `repo:` — hiding that costs about ten points of recall, so roughly seventy
  five of its eighty six comes from reading the thread and the rest from
  repeating the metadata. Measured rather than argued; see RESULTS.md.
- **Scores move between identical runs, and one task moves much more.** The same
  configuration run twice gave the same `thread_package` gold set both times
  (94 edges) and a `dependencies` gold set that nearly halved (66, then 37). A
  Wilson interval assumes a fixed gold set, so the stated ±13 on that task
  understates its real uncertainty.
- **Nothing is reranked.** The hybrid figure is fusion of three retrievers, not
  a cross-encoder over their output, and the published table this project
  declines to compare against gains its largest jump from exactly that.
- **The ablation covers two packs and they disagree by a factor of three** —
  26.8% recoverable on news, 7.6% on case law. Both are clean now, and neither
  says whether an end-to-end system *answers* the question. Name presence is a
  lower bound: being in the retrieved text is necessary for a reader to assemble
  an answer, not sufficient.
- **Article resolution now leans on the extractor's own edges.** 282 article
  mentions resolve through a `HAS_ARTICLE` the model asserted, which is evidence
  rather than proximity — but it does mean the resolution rate for that type is
  bounded by extraction quality in a way the other types' is not.
- **And the rule that made it work was written after reading this corpus.** The
  `article_number` normaliser's shape came from looking at unresolved mentions,
  and the decision to build the feature came from simulating it against the
  documents it is scored on. `tr-law` therefore sets `holdout: 0.3` and the task
  scores 71.3% held out against 69.6% in-sample — higher, so the rule was not
  fitted to what it is measured on. A real holdout would have fixed the split
  before the design; this one is a check, not a clean measurement, and RESULTS.md
  says so.
- **One machine for everything local.** M4, 16 GB. Every timing here that is not
  a hosted model is a fact about that machine, and the local extraction quality
  (llama3.1:8b, 17.8% conforming) is a fact about a small model rather than
  about the design.

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
