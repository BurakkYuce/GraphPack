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
dependencies (backbone_edges: DEPENDS_ON)      # ollama, dynamic extractor
  P 17.6% [8.3–33.5]   R 30.0% [14.5–51.9]   F1 22.2%
  tp 6, fp 28, fn 14 — 20 gold edges, 17 of 147 documents carried gold
```

This run was later repeated on the hosted model with the ontology enforced. The
score did not improve; see "The controlled comparison" below. The analysis that
follows is what still holds, and it holds for both runs.

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

The obvious reading of that funnel is the fourth row: half of the package
mentions are packages the top-1,000 backbone does not contain, so widen the
backbone. That was written here as the fix, and then run.

**It bought two gold edges.**

| | top 1,000 | top 8,000 |
|---|---:|---:|
| backbone packages | 1,000 | 7,999 |
| backbone `DEPENDS_ON` edges | 2,437 | **25,367** |
| mentions resolving to a provisional node | 83 | 69 |
| documents carrying gold | 19 | 20 |
| **gold edges** | **22** | **24** |
| F1 | 15.4% | 14.6% |

Ten times the edges, two more gold. So the ceiling was never backbone coverage,
and one query says what it is instead:

```
documents mentioning exactly 1 backbone package    96
                            2                      24
                            3                       9
                            4 or more              10
```

**Sixty-nine percent of the corpus discusses one package.** `backbone_edges`
needs a document that mentions *two* entities the backbone relates, and a GitHub
issue thread is about one library having one problem. No backbone makes that
corpus produce pairs it does not contain.

This is the difference between the two packs, and it is not about domain
difficulty. tr-law scores 150 gold edges from the same 200 documents because its
documents *are* nodes: a decision cites a statute, so every citation is a
scoreable fact about that document. oss's documents are not in its graph, so gold
has to come from coincidence — two related packages happening to appear in the
same thread — and coincidence is rare.

**The generator, not the corpus, is what to change.** `document_edges` scores a
document against what the backbone says it points at; `backbone_edges` scores a
pair against a document that mentions both. The first is available whenever
documents are entities. oss's were not: its corpus was issue threads and its
backbone packages, with no edge between them.

That prediction was written here before it was run, and it was then run — see
[Giving oss its documents](#giving-oss-its-documents-back) below. It holds: an
`Issue` node per thread took the gold set from 24 edges to 135 and the interval
from ±13 points to about ±6, as pure configuration. It also exposed a limit of
the new task that the prediction did not anticipate, which is written up there.

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

## Giving oss its documents back

```bash
uv run graphpack backbone load oss     # Issue nodes + MENTIONS_PACKAGE edges
uv run graphpack eval oss              # both tasks, one extraction run
```

The change is configuration: three load steps in `domains/oss/sources.yaml`
building an `Issue` node per thread and an edge to the package its repository
publishes, and one task in `eval.yaml` using `document_edges`. No GraphPack code
and no re-extraction — this scores the *same* run as everything above, so the
only variable is the gold generator.

| | `dependencies` | `thread_package` |
|---|---|---|
| generator | `backbone_edges` | `document_edges` |
| asks | did extraction find two *related* packages in one thread? | did it find the package the thread is about? |
| gold edges | 24 | **135** |
| documents carrying gold | 20 of 139 | **135 of 139** |
| precision | 17.6% [6.2–41.0] | 52.8% [46.1–59.3] |
| recall | 12.5% [4.3–31.0] | **85.2%** [78.2–90.2] |
| F1 | 14.6% | 65.2% |
| interval width | **±13 points** | **±6 points** |

**The interval is the result.** oss could not be measured before — ±13 points on
24 edges supports no conclusion, which is what this document said and still
says. It can be measured now, and the cost was configuration.

### Read the precision with its ceiling

**52.8% is not an error rate, and the tool now says so.** Gold holds exactly one
package per thread, because the repository list is deduplicated by slug and each
repository is credited to one package. Extraction resolves *every* package a
thread discusses: 218 (document, package) pairs against 135 that can possibly be
gold. So precision is capped at **61.9%** by construction, and the measured
52.8% leaves 9 points of headroom rather than 47.

Most of that 38% excess is correct reading. A thread in `aio-libs/aiobotocore`
mentioning `boto3`, `botocore` and `awscli` is scored with one true positive and
three false ones, and all four readings are right.

`graphpack eval` prints the cap whenever it binds, because a capped precision
read as an error rate is exactly the kind of wrong number this project treats as
worse than an error.

### The easier question, said plainly

`thread_package` asks something easier than `dependencies` did, and the two are
kept side by side so that is visible rather than buried. 85.2% recall on "is the
thread's own package named in the thread" is a real measurement of extraction
and resolution end to end, and it is not the same measurement as "did the model
find a dependency relation", which remains at 12.5% on the same run.

What the harder task cannot do is *support* a number, and that is the difference
worth having. Both are reported.

### Two caveats, both small and both real

**Four of 139 documents have no `Issue` node.** They were ingested from an
earlier `issues.jsonl`; GitHub's `sort=comments` ordering drifts between fetches,
so a re-fetch does not return the same page. Those four are excluded from gold
rather than counted as misses. A pack wanting strict reproducibility should
record the fetch date in `data/MANIFEST.txt` and re-ingest from the same file.

**Monorepos add noise, not bias.** One package is credited per repository, so a
thread in a repository that publishes several is scored against whichever one
the derive step kept. It is a limit of deriving this from published metadata
alone, and it is stated rather than corrected.

## A third of the graph was the prompt we wrote

```bash
# domains/oss/sources.yaml — corpus block
#   hide_from_model: [url, state, created_at]
uv run graphpack pack reset oss --extraction-only --yes
uv run graphpack ingest oss --sample 200 --seed 0
uv run graphpack resolve oss && uv run graphpack eval oss
```

LlamaIndex prepends every metadata key to a chunk as `key: value` before a model
sees it, so a field kept for bookkeeping is a field extraction reads as document
content. This was known — it is how an entity called `pack: oss` appeared — and
the mechanism to prevent it (`hide_from_model`) existed and was deliberately not
used, so the committed configuration matched the run that produced the published
numbers. Re-running costs four minutes on a hosted model. That reason expired.

**Measured before the change, on the schema path with constraints enforced:**

```
  490  extracted entities
  154  named by a URL      31.4%   typed ISSUE, PACKAGE, REPOSITORY
    0  named by a date      0.0%
    0  named open/closed    0.0%
```

Nearly a third of the graph was the `url:` line, and `validate-triples` reported
**100% conforming** throughout — because those entities were typed `ISSUE`,
`PACKAGE` and `REPOSITORY`, all of which the ontology declares. Conformance
checking cannot see this class of error at all.

Two corrections to what this document previously predicted. It said the
contamination showed up as `URL` and `DATE` entity types; that was true of the
local run on the dynamic extractor, and on the schema path there is no such
label because non-conforming types are discarded before they are written. And
`state` and `created_at` produced nothing measurable either way — only `url`
did. They are hidden anyway, since text that reaches the model without being
content costs tokens and buys nothing.

### What hiding it did

| | url visible | url hidden |
|---|---:|---:|
| chunks | 604 | 518 |
| extracted entities | 490 | 419 |
| **named by a URL** | **154 (31.4%)** | **3 (0.7%)** |
| REPOSITORY | 20 | **77** |
| HOSTED_IN | 10 | **85** |
| REPORTED_IN | 15 | **52** |
| AUTHORED | 0 | 4 |
| conformance | 100% | 100% |
| `dependencies` F1 | 14.6% | **21.1%** |
| `thread_package` recall | 85.2% | 86.2% |

The graph got smaller and better. Removing the URL line did not just delete
noise, it freed the extractor to find structure it had been spending its budget
missing — repository and authorship edges roughly quintupled, and the dependency
task's F1 went from 14.6% to 21.1%.

Chunks fell 14% as well, because metadata counts against the chunk-size budget.
Fewer chunks is fewer model calls, so this is cheaper as well as cleaner.

The three surviving URL-named entities come from links in the issue bodies,
which is genuine document content.

### The new task leaks, and here is how much

`thread_package` gold is derived from the repository slug, and the model is
shown `repo: <slug>`. So its 86% recall might be the model repeating metadata
back rather than reading the thread. That is worth knowing rather than assuming,
and settling it cost one more four-minute run:

| hidden from the model | `thread_package` recall | `dependencies` F1 |
|---|---:|---:|
| `url, state, created_at` | **86.2%** [77.8–91.7] | 21.1% |
| `url, state, created_at, repo` | **74.8%** [65.8–82.0] | 12.5% |

**About ten points of that recall is the slug; about seventy-five survives
without it.** So the task is not metadata echo, and it is not clean either — and
the size of the effect is now a number rather than a worry.

`repo` stays visible in the committed configuration, and the reason is not that
it scores better. A GitHub issue is inseparable from its repository: a reader
sees it in the URL bar and in every cross-reference, so it is document content
in the way that `created_at` is not. Hiding content to improve a score would be
fitting the corpus to the evaluation. The leak is handled by measuring it and
writing it here.

### Run-to-run variance, measured for the first time

The configuration above was run twice, identically. This has never been checked
in this project and it changes how one of the two tasks should be read:

| | run A | run B |
|---|---:|---:|
| extracted entities | 422 | 419 |
| documents with resolved entities | 94 | 94 |
| `thread_package` gold | **94** | **94** |
| `thread_package` recall | 85.1% | 86.2% |
| `dependencies` gold | **66** | **37** |
| `dependencies` F1 | 22.0% | 21.1% |

**`thread_package` is stable and `dependencies` is not.** The document-shaped
gold set was identical across runs — it depends only on which documents resolved
anything at all. The pair-shaped gold set nearly halved, because it depends on
which *specific pairs* extraction happened to find in the same document, and
that varies with the model's sampling.

This matters for how the intervals are read. A Wilson interval assumes a fixed
gold set and reports the sampling error in the score; when the gold set itself
moves by 44% between runs, the stated ±13 points on `dependencies` understates
the real uncertainty. Its F1 landing at 22.0% and 21.1% across those two runs is
partly luck.

So the case for the document-shaped generator is stronger than the interval
width alone suggested: it is not merely a narrower measurement, it is a
*reproducible* one.

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

## The tr-law corpus — the thesis's second domain

```bash
uv run graphpack ingest tr-law --sample 200 --seed 0   # 10 m 19 s, gemini-3.5-flash-lite
uv run graphpack resolve tr-law
uv run graphpack eval tr-law
```

```
statute_citations (document_edges: CITES)
  P 97.2% [92.1–99.1]   R 70.0% [62.2–76.8]   F1 81.4%
  tp 105, fp 3, fn 45 — 150 gold edges, 88 of 88 documents carried gold
```

**150 gold edges against oss's 20 at the time**, and intervals of ±5 rather than
±13. Two things produce that, and only one of them is the model.

The first is the pack's shape — and specifically that its documents are nodes.
tr-law's backbone is built from the citations in the decisions themselves, so
every statute a decision cites is in the backbone by construction. Every one of
the 88 documents with a resolved entity carried gold; for oss it was 17 of 147.

That difference is the one this document later stopped attributing to the
domain: giving oss's threads document nodes and scoring them the same way took
it from 17-of-147 to 135-of-139 without touching the model, the corpus or the
extraction run. What looked like two domains of unequal difficulty was, in that
respect, two *generators* of unequal reach. The remaining gap between 81.4% and
oss's tasks is real, and smaller than it was.

The second is the extractor. See below.

### Precision 97.2% is the schema constraint, not the model

Three false positives out of 108 claims. That is what enforcing
`rdfs:domain`/`rdfs:range` during extraction buys: a triple whose types the
ontology does not pair is discarded before it is ever written.

The cost is on the other side. 45 of 150 gold edges were missed, and every one
of the ten sampled misses is the same shape — *"both ends resolved, nothing
between them"*. The model named the decision and named the statute and did not
say the decision cites it. Recall is where a strict schema is paid for.

### The controlled comparison — and it refutes what this section first claimed

The obvious reading of tr-law's 81.4% against oss's 22.2% is that the hosted
model and the enforced ontology did it. That reading was written here, and then
tested: oss was re-run on tr-law's exact setup — same model, same schema
extractor, same installed constraints, same 200 documents, same seed.

| oss | ollama, dynamic | gemini, schema |
|---|---|---|
| wall clock | 10 h 37 m | **4 m 52 s** |
| relations conforming | 17.8% | **100%** |
| entity labels the ontology declares | 42% | **100%** |
| gold edges | 20 | 22 |
| precision | 17.6% | 17.6% |
| recall | 25.0% | 13.6% |
| **F1** | **22.2%** [±13] | **15.4%** [±13] |

**The extractor change bought conformance and not one point of F1.** Precision
is identical to the decimal. Recall halved — the strict schema discards the
near-misses the dynamic extractor was credited for. The two F1 figures are
inside each other's intervals, so the honest statement is that changing the
model and enforcing the ontology *did not measurably improve the score on this
pack*, and may have cost recall.

So the tr-law/oss gap is not the model and not the extractor. It is the pack:

| | oss | tr-law |
|---|---|---|
| backbone built from | published metadata, top 1,000 packages | the corpus's own citations |
| documents with a resolved backbone entity | 135 | 88 |
| …of those, carrying gold | **19** | **88** |
| gold edges | 22 | 150 |

tr-law's backbone covers its corpus by construction — every statute a decision
cites is in the backbone because that is where the backbone came from. oss's
covers a tenth of what its corpus discusses, so five sixths of its documents
have entities but no scoreable pair. That was the finding oss's own error
analysis pointed at, and the controlled run is what confirms it was the whole
story rather than one of three.

What the extractor change *did* buy is a graph that means what its ontology
says: 100% of relations conforming, zero invented entity types, on both packs.
That is worth having and it is not visible in F1.

Extraction cost about **$0.60** per pack at `gemini-3.5-flash-lite` rates.

### Article citations score nothing, for a structural reason

The second task, `article_citations`, has no gold at all: 96 of 97 extracted
`ARTICLE` mentions fail to resolve. This is not a threshold to tune.

Backbone article identifiers carry their statute — `madde:6100/371` — because an
article number alone identifies nothing; article 371 of *which* law. The
extracted mentions are `"371. maddesinde"`, `"16/son"`, `"4. madde birinci
fıkrası"`. The statute is in the sentence around them, and extraction kept the
article and discarded the sentence.

Resolving these needs context-dependent resolution: reading a mention against
the entity it was extracted alongside, rather than on its own. The ontology
already declares `STATUTE HAS_ARTICLE ARTICLE`, so the information is
expressible; nothing in the resolver reads it yet. That is the honest state —
the number is absent rather than bad.

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
machinery, same intervals, 2,255 measurements instead of 24: ±2 points instead
of ±13. Nothing about the system got more certain — the measurement did. And
the same lesson applied to oss later, at a smaller scale: 135 measurements
instead of 24 narrowed it to ±6, again without changing the system.

### What this is a number for, exactly

- **Vector retrieval only.** The engine's BM25 leg is an in-memory docstore
  belonging to the process that ingested, so a benchmark run as a separate
  command has no full-text half. `LI BM25: get_retriever() returned None` is the
  engine saying so. `graphpack bench bench-wiki --ingest --hybrid` now does both
  in one process and scores the fusion retriever, so the number the table above
  lacks is a command rather than a design change.
- `nomic-embed-text`, chunk size 1024, overlap 128, top-30 retrieved.
- **Chunks are reduced to articles.** Several chunks of one article are one
  result, taking the rank of its best chunk. Counting chunks would make Hit@10 a
  measure of how finely an article was split.
- **No comparison to the published table is made here**, and the reason is
  worse than it looked. See the next section.

### The published table, and why our number is not above it

This document has said twice that a comparison to MultiHop-RAG's own numbers was
"one run away" — match the embedding model and the reranker and the two tables
line up. Reading the paper says otherwise. Here is what it reports
([arXiv:2401.15391](https://arxiv.org/abs/2401.15391), Table 5), and here is
ours:

| | MRR@10 | Hits@10 | Hits@4 |
|---|---:|---:|---:|
| ada-002, no reranker | 0.4203 | 0.6381 | 0.5040 |
| bge-large-en-v1.5, no reranker | 0.4298 | 0.6718 | 0.5221 |
| voyage-02 + bge-reranker-large — their best | **0.5860** | 0.7467 | 0.6625 |
| **this project, `nomic-embed-text`, no reranker** | **0.759** | 0.977 | 0.909 |

A number 30% above the best published row, from a smaller open embedding model
and no reranker, is not a result. It is a sign that two quantities have been
given the same name, and they have — in two ways, both ours:

**They score chunks; we score articles.** Our runner deliberately reduces
retrieved chunks to the articles they came from and ranks those, which is
documented above as the right thing for the question we were asking. It also
makes the task easier: thirty retrieved chunks collapse to far fewer distinct
articles, and asking whether a gold *article* is among them is a weaker demand
than asking whether a gold *chunk* is.

**Their Hit@K is recall over the evidence set; ours is "found at least one".**
The paper defines it as "the fraction of evidence that appears in the top-K
retrieved set". A query resting on four articles scores 0.25 for them when one
is found, and 1.0 for us. A test in this repository asserted the opposite —
that ours was "the quantity the paper reports" — and that comment is now
corrected rather than deleted.

So the honest statement is not that this system retrieves better than the
published baselines. It is that **these numbers measure an easier task and are
not comparable**, and every earlier sentence in this repository implying the gap
was one embedding model wide was wrong.

**What a real comparison needs**, now specific rather than gestured at:

1. *Chunk-level gold.* The data supports it — each evidence entry in
   `queries.jsonl` carries its `fact` sentence, so a gold chunk is one whose
   text contains that sentence. The pack's derive step currently keeps only the
   article, which is what made article-level scoring the natural thing to build.
2. *Their Hit@K.* Recall over the evidence set rather than any-one-hit.
3. *Their retrieval depth*, 20 chunks rather than our 30.
4. *A matching embedding model*, and optionally `bge-reranker-large`.

The first three are code and cost nothing to run. Only the fourth needs anything
this project does not have.

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

## Does the graph answer what retrieval cannot?

```bash
uv run graphpack ablate bench-wiki
```

The project had claimed, in prose, that some questions are joins rather than
passages. This is that claim as a number. The traversal's answer *defines* the
question — scoring the graph against its own output would be circular — and the
only thing measured is how much of that answer a reader could recover from the
retrieved text alone.

**bench-wiki, top-30, all 609 articles indexed:**

| question | answer set | recovered | share |
|---|---:|---:|---:|
| articles from CNBC | 8 | 6 | 75% |
| outlets covering technology | 9 | 5 | 56% |
| outlets covering tech (abbrev) | 9 | 4 | 44% |
| outlets covering sports | 18 | 3 | 17% |
| articles from TechCrunch | 51 | 6 | 12% |
| articles from The Verge | 46 | 5 | 11% |
| articles by Natasha Lomas | 15 | 0 | 0% |
| articles by Sarah Perez | 13 | 0 | 0% |
| **mean** | | | **26.8%** |

Two patterns, and neither is "retrieval is bad".

**Recovery falls as the answer set grows.** Eight answers: 75%. Fifty-one: 12%.
Nothing about the retriever changed between those rows — what changed is
whether the answer fits in thirty passages. That is the difference between a
question with an answer and a question with a *join* for an answer, and it is
the whole claim.

**The byline questions score zero, for a reason worth stating.** `author` is not
in the metadata this pack declares, so it is in no passage at all. The graph has
it because the backbone was loaded from the article records. This is not
retrieval failing at a hard question; it is information that exists in the
structured half and nowhere in the text — the plainest version of the case for
having a backbone.

Retrieval was measured **as a reader sees it** — `MetadataMode.LLM`, so the
title and outlet prepended to every chunk count as present. Scoring the bare
body instead gave 11.4%, and would have flattered the graph by more than a
factor of two. That is the one direction this measurement must not be wrong in.

### tr-law's ablation is confounded — reported, not used

The same command on tr-law returns 5.9%, and the number should not be quoted.
Its graph holds all 1,578 decisions while only 200 were ingested, so most of
each answer is not in the vector store to be found. That gap is sampling, not
structure. bench-wiki has no such confound: every one of its 609 articles is
indexed, which is why it is the measurement above.

## What has not been measured

- **Article-level citations.** No gold survives resolution; see above.
- ~~**oss under a gold generator that fits it.**~~ Done — see [Giving oss its
  documents back](#giving-oss-its-documents-back). It was pure configuration and
  it worked: 24 gold edges to 135, ±13 points to ±6. What it does *not* do is
  make `dependencies` measurable; that task still has 24 edges and still
  supports no conclusion, and the new task answers an easier question.
- ~~**oss's prompt contamination.**~~ Measured and fixed — see [A third of the
  graph was the prompt we wrote](#a-third-of-the-graph-was-the-prompt-we-wrote).
  It was 31.4% of the entities, invisible to conformance checking, and removing
  it improved the dependency task rather than merely shrinking the graph.
- **tr-law's ablation, unconfounded.** Its graph holds 1,578 decisions and 200
  are ingested, so the 5.9% is mostly sampling. Ingesting the full corpus would
  make it a second clean data point beside bench-wiki's 26.8%.
- **Hybrid retrieval.** Every benchmark number above is the vector leg alone.
  `graphpack bench <pack> --ingest --hybrid` now scores the fusion retriever —
  the command exists because the BM25 docstore lives in the object that
  ingested, so the two have to happen in one process. The number is a run, and
  it is not in this document until it has been run.
- **Run-to-run variance on anything but oss.** Measured there for the first time
  and it mattered: the same configuration twice gave a `dependencies` gold set
  of 66 and then 37. Nothing here says whether tr-law or the benchmark move
  that much, and the intervals throughout this document assume they do not.
- **The published MultiHop-RAG table.** Not a run away, as this document twice
  claimed — the metrics differ. Ours scores articles where the paper scores
  chunks, and our Hit@K is "found at least one" where the paper's is recall over
  the evidence set. See [The published table](#the-published-table-and-why-our-number-is-not-above-it)
  for what closing that actually requires.
- **`strict_schema` true versus false.** The sweep this phase planned is not
  worth running: on the dynamic extractor the setting is inert, and the section
  above is the evidence.
- **The holdout.** `eval.yaml` sets `holdout: 0.0` with a note to raise it
  before error analysis feeds back into `aliases.csv` or a normaliser. Nothing
  in this phase did: both fixes were defects — a contaminated prompt and an
  arbitrary label choice — and neither was derived from which gold edges were
  missed. The holdout stays at 0 and the note stands for the first change that
  does chase a miss.
