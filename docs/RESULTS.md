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

**Which extraction run this scores, since the graph has since been replaced.**
The table above is the ollama/dynamic run — 3,528 entities from 200 documents.
[The controlled comparison](#the-controlled-comparison-and-it-refutes-what-this-section-first-claimed)
below re-ran the same 200 documents on gemini with the schema extractor, and
that is what the database holds today. Re-running `eval oss` therefore does not
reproduce the numbers above; it reproduces these:

| `thread_package` | ollama, dynamic | gemini, schema |
|---|---:|---:|
| entities extracted | 3,528 | **419** |
| gold edges | 135 | **94** |
| precision | 52.8% [46.1–59.3] | 43.1% [36.2–50.2] |
| precision ceiling | 61.9% | 50.0% |
| **recall** | **85.2%** [78.2–90.2] | **86.2%** [77.8–91.7] |
| F1 | 65.2% | 57.4% |

`dependencies` moved the same way and for the same reason: 24 gold edges to 37,
precision 17.6% to 30.0%, recall 12.5% to 16.2%. Both intervals are still ±13
points wide, so that task still supports no conclusion — which is the finding it
has supported since phase 4.

**Recall is the row that matters and it does not move** — 85.2% against 86.2%,
each inside the other's interval. Precision falls with its ceiling, and the
ceiling falls because gold shrank: gold needs a resolved package, the schema
extractor produces an eighth as many entities, so fewer documents are eligible.
That is the self-labelling design showing its cost — the gold set is not
independent of the extractor being scored — and it is the clearest instance of
it in this repository.

This is what the phase-review round is for: the check is to re-run what should
not have changed, and the finding was that the graph underneath a published
table had been replaced two phases earlier.

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

### The same measurement at eight times the scale

The sample above is 200 of 1,578 decisions. Running all of them is the test of
whether that sample was measuring anything:

```bash
uv run graphpack pack reset tr-law --extraction-only --yes
uv run graphpack ingest tr-law          # no --sample: all 1,578
uv run graphpack resolve tr-law && uv run graphpack eval tr-law
```

| | 200 documents | **1,578 documents** |
|---|---|---|
| chunks | 611 | 4,689 |
| extracted entities | — | 5,130 |
| conforming relations | 100% | **100%** (8,605 of 8,605) |
| documents carrying gold | 88 of 88 | **711 of 711** |
| gold edges | 150 | **1,242** |
| precision | 97.2% [92.1–99.1] | **97.0%** [95.6–97.9] |
| recall | 70.0% [62.2–76.8] | **69.2%** [66.6–71.7] |
| interval, precision | ±3.5 points | **±1.2** |
| interval, recall | ±7.3 points | **±2.6** |

**The scores held to within a point at eight times the data.** Precision 97.2 →
97.0, recall 70.0 → 69.2, F1 81.4 → 80.8, all well inside the old intervals. That is the
strongest reproducibility evidence in this document, and it is worth putting
beside oss's `dependencies` task, whose gold set nearly halved between two runs
of the *same* configuration. Same machinery, same metrics code: one measurement
is stable and the other is not, and the difference is what the gold is made of.

Every one of the 711 documents with a resolved statute carried gold, as all 88
did before. That is the property of this pack that makes it measurable: its
backbone is built from the citations in the decisions themselves.

Cost: 55 minutes end to end — 7 minutes chunking and embedding, 25 minutes of
extraction on `gemini-3.5-flash-lite`, and **23 minutes writing the property
graph**, which is the part nobody budgets for. See ENGINE.md.

### The holdout this pack's own rule demanded

Everything above is scored on the whole corpus, and for one of the two tasks
that stopped being honest. `domains/oss/eval.yaml` wrote the rule down before
any of this happened:

> A holdout protects against rules fitted to the data being scored. […] That
> changes the moment the error analysis feeds back into `aliases.csv` or a
> normaliser.

That moment arrived with the `context:` work below. Its `article_number`
normaliser was written by looking at unresolved mentions *in this corpus*, and
the decision to build the feature at all came from simulating it against these
documents. So `tr-law` now sets `holdout: 0.3`, split by decision, and both
numbers are here:

| | whole corpus | held-out 30% |
|---|---:|---:|
| `statute_citations` P | 97.0% [95.6–97.9] | 98.0% [95.5–99.2] |
| `statute_citations` R | 69.2% [66.6–71.7] | 66.0% [61.0–70.6] |
| `statute_citations` F1 | **80.8%** | 78.9% |
| `article_citations` P | 63.6% [60.6–66.6] | 67.6% [61.9–72.8] |
| `article_citations` R | 76.8% [73.8–79.6] | 75.4% [69.7–80.3] |
| `article_citations` F1 | **69.6%** | **71.3%** |
| scored on | 711 / 638 documents | 213 / 191 |

**The task under suspicion scores *higher* held out — 71.3% against 69.6%.** So
the normaliser was not fitted to the documents it is measured on; if anything
the held-out decisions are slightly easier. Every interval overlaps between the
two columns, so the honest reading is that the split changed nothing, which is
the answer the check existed to get.

`statute_citations` drifts down 1.9 points on a third of the data, with a wider
interval to match. No normaliser was written for it, and nothing here suggests
one is needed.

**What this does not buy.** A real holdout fixes the split *before* the design.
This one was applied after, and the design had already seen the whole corpus, so
the held-out column is a check rather than a clean measurement. What it does
check is the specific thing at risk: the mentions I actually read are almost all
in the other 70%. And the normaliser is generic — the first one-to-three digit
number, which is the shape every article citation in the language has — rather
than something shaped around particular misses.

The committed configuration keeps `holdout: 0.3`. Turning the check off after it
came back favourable would be the one move that makes it worthless.

### There are three levels here, and only one of them is the claim

Yesterday's finding was that `document_edges` scored mentions while declaring a
relation. Adding the strict version exposed a second gap in the same place: it
accepts *any* subject the document's chunks mention. "Some entity in a chunk of
this decision cites statute S" is not "this decision cites statute S" — the
subject may be a party, a lower court, or something that resolved to nothing.

So the contract now expresses all three, and each is a task a pack opts into:

| level | a prediction is | tr-law, `CITES` |
|---|---|---:|
| `mentions` (default) | the document's chunks name something resolving to the target | P 98.4% · R 65.4% |
| `require_relation` | *something* in those chunks relates to the target | P 82.9% · R 26.2% |
| `require_subject` | **the document itself** relates to the target | P 75.0% · **R 11.3%** |

and on `CITES_ARTICLE`, P 65.1/R 73.6 → P 67.8/R 32.7 → P 55.1/**R 16.2%**.

**Extraction almost never makes the document the subject of its own citation.**
Counting predictions over the whole corpus, before any verification pass:

| | any subject | **subject is the document** |
|---|---:|---:|
| `CITES` | 172 | **28** |
| `CITES_ARTICLE` | 247 | **25** |

Twenty-eight, against 1,242 gold edges. And `article_citations_subject` scored a
flat **0.0%** before the verification pass ran, because extraction had produced
no `Decision -CITES_ARTICLE-> Article` edge at all — every one of its strict
predictions came from a subject that was not the decision.

**oss cannot reach this level, and the reason is configuration.** Its
`MENTIONS_PACKAGE` subjects are 128 unresolved entities and one `Repository`;
none is an `Issue`, because the pack declares no resolve rule for `ISSUE` —
which `packs validate` has warned about all along, as a note nobody had a number
for. So oss's 43.6% strict recall is entirely earned by edges whose subject is
not the thread, and its subject-level task scores zero by construction. The
warning now has a number attached, which is the difference between a note and a
finding.

**Which level should be quoted?** The last one, when the question is "does the
graph say this document cites that statute". The first, when the question is
"can extraction find the statutes a decision names" — and it answers that
extremely well. The middle one is not a resting place; it exists because a query
happened to stop there, and it is kept only so the numbers published under it
remain checkable.

### A second pass doubles relation recall, and its ceiling is somewhere else

Extraction reads a chunk once and has to produce every entity *and* every
relation from it. tr-law shows those two going very differently: 98.4% precision
naming the statutes, 13.1% recall drawing the edge. So the edges were asked for
separately — one question, one pair, with the text in front of the model.

```bash
uv run graphpack verify tr-law --task statute_citations_strict
uv run graphpack eval tr-law
```

| `statute_citations_strict` | extraction alone | **+ verification pass** |
|---|---:|---:|
| precision | 90.9% [80.4–96.1] | 82.9% [75.3–88.6] |
| **recall** | **13.1%** [10.0–16.8] | **26.2%** [22.1–30.8] |
| F1 | 22.8% | **39.8%** |

**Doubled, for twelve minutes on a local model and no money.** 372 candidate
pairs, 255 confirmations — a 68.5% confirmation rate — and 243 distinct edges
written. Precision paid eight points for it, which is the trade and is visible
rather than absorbed.

The same pass on `article_citations_strict`: 347 candidates, 218 confirmations
at 62.8%, recall 24.2% → **32.7%**. And it produced the pack's first
`Decision -CITES_ARTICLE-> Article` edges of any kind — that subject-level task
read 0.0% before it ran. 456 verified edges across both.

**The honesty constraint is the whole design.** Candidates come from `MENTIONS`,
never from the backbone. Choosing which pairs to ask about by reading the gold
would write the answers into the graph and then score against them, and the
result would look exactly like an improvement. A test asserts the candidate
query never traverses a backbone edge, because that is the kind of mistake that
is invisible in the output.

**And every number this graph now produces includes those edges**, so `eval`
says so on every run:

```
243 edge(s) above came from `graphpack verify`, not from extraction.
Remove them with `graphpack verify tr-law --forget` to score extraction alone.
```

**The ceiling was measured before the run, and it is not the relation.** An edge
needs a subject, so this pass can only reach a statute in a chunk that also
carries an extracted entity resolving to the *decision*:

```
4,209  chunks with any mention
  896  ...mentioning a Statute
  315  ...also mentioning a Decision   -> 35%
```

Two thirds of the statute-bearing chunks have nothing to attach a citation to.
That is an entity-extraction gap wearing a relation-extraction gap's clothes,
and it was worth twenty seconds of Cypher to find out before spending the run.
It also predicts where the remaining recall is: not in asking better questions,
but in extracting the decision itself more often.

### oss keeps four times as many of its relations as tr-law does

The strict/loose split was applied to `oss` as well, and the contrast is the
useful part:

| | scores | precision | recall |
|---|---|---:|---:|
| `thread_package` | mentions of `Package` | 43.1% | 86.2% |
| **`thread_package_strict`** | **`MENTIONS_PACKAGE` extracted** | 37.3% | **43.6%** |
| tr-law `statute_citations_strict` | `CITES` extracted | 90.9% | 13.1% |

**43.6% against tr-law's 13.1%**, from the same extractor and the same model.
Whatever costs tr-law its relations is not a property of the pipeline — it is a
property of the pack, or of the language, or of what "cites" looks like in a
court decision against what "mentions" looks like in an issue thread. That is a
question this split can now be pointed at, and could not be before.

The precision direction is the reverse. tr-law draws few `CITES` edges and is
right 90.9% of the time; oss draws many `MENTIONS_PACKAGE` edges and is right
37.3%. Cautious and silent against talkative and wrong, measured rather than
characterised.

### The task declared a relation and never checked it

`statute_citations` is configured `relation: CITES` and reported 97.0%
precision against 1,242 gold edges. The graph holds **170 `CITES` relations in
total**. Those two facts cannot both be about the same thing, and following that
arithmetic is how this was found.

`document_edges` never read `task.relation`. A prediction was *"a chunk of this
document mentions an entity that resolved to a node of the right label"* — the
declared relation was parsed, stored, printed in the task's own description, and
never used for anything but locating the gold. Two phases of numbers under it.

**The score was not wrong; its name was.** Mention-and-resolution is a real
measurement of a real thing — did the document name a statute a reader could
identify — and it is the harder half of what most pipelines get wrong. It is
just not relation extraction, which is what `CITES` sounds like.

So both are now measured, over the same documents:

```bash
uv run graphpack eval tr-law     # four tasks: two loose, two strict
```

**On the held-out 30%**, which is what this pack scores — the full-corpus figures
elsewhere in this document are the loose tasks only, and the strict pair has no
full-corpus run yet.

The gold columns are 382 against 383, and the one-edge difference is not a typo:
the holdout draws its subjects from the union of gold and *predictions*, so
changing what a task predicts moves the split by a document. Same corpus, same
denominator rule, splits that differ by one. Worth knowing before treating the
two rows as strictly paired.

| | scores | precision | recall | gold |
|---|---|---:|---:|---:|
| `statute_citations` | mentions of `Statute` | 98.4% | 65.4% | 382 |
| **`statute_citations_strict`** | **`CITES` extracted** | 90.9% | **13.1%** | 383 |
| `article_citations` | mentions of `Article` | 65.1% | 73.6% | 261 |
| **`article_citations_strict`** | **`CITES_ARTICLE` extracted** | 75.7% | **24.2%** | 231 |

**Relation extraction recovers about an eighth of the citations.** The model
finds the statute — entity extraction and resolution are the strong half — and
then does not draw the edge. When it does draw one it is usually right, 90.9%,
so this is pure recall and there is nothing subtle about where it goes.

That reframes what this pack's headline meant. `81.4% F1` and the 97% precision
beside it are mention-level, and everything this document has said about tr-law
being the well-behaved pack remains true of *that* quantity. The relation-level
number had never been taken.

**The near-miss, since it is the more useful half.** The first working version of
the strict task read **59.0%** recall, not 13.1%. `document_edges` restricts gold
to documents that were actually ingested, and it proxies that with "documents
having a resolved mention" — so deriving the proxy from the *strict* prediction
dropped every document extraction had failed on out of the gold set, and
reported recall over the documents it had already succeeded on. Both tasks now
share the loose denominator, and a test fails if that is undone.

### What the second task still cannot measure

```
article_citations (document_edges: CITES_ARTICLE)
No gold edges. 4,689 chunk(s) are ingested, and no mention of type Article
resolved to the backbone.
```

483 `ARTICLE` mentions were extracted and essentially none resolve. The pack
says why in `resolve.yaml`, and it is a deliberate refusal rather than a gap:

> A bare "369. madde" is left alone rather than guessed at: attaching it to
> whichever statute was last mentioned would manufacture citations, and
> manufactured citations are exactly what the evaluation is meant to detect.

An article number identifies nothing on its own — 371 of *which* statute. The
backbone writes `madde:6100/371`; the mention is `"371. maddesinde"`, and the
statute is in the sentence extraction discarded.

What makes this fixable rather than fundamental: extraction produced **546
`HAS_ARTICLE` edges** from statutes to articles. Those are the model's own
claims about which statute an article belongs to, not proximity guesses, so
resolving a bare article through one is a different thing from the heuristic the
pack refuses.

### Resolving through a relation extraction claimed

The payoff was measured before anything was written, by simulating what the
feature would build:

```
  546  extracted HAS_ARTICLE pairs
  371  from a statute that resolved to an article that did not
  282  that would build an id the backbone actually holds
```

Then built — a `context:` block in `resolve.yaml`, a second pass after the
rule's own methods — and it resolved **282**, the simulated number exactly.

```yaml
  - entity: ARTICLE
    methods: [exact]
    context:
      via: HAS_ARTICLE          # the edge extraction produced
      from: STATUTE             # what the other end must be
      id: "madde:{source|statute_number}/{name|article_number}"
```

| | before | after |
|---|---|---|
| mentions resolved | 1,276 of 3,050 (41.8%) | **1,558 of 3,050 (51.1%)** |
| `article_citations` gold | **none** | **802 edges**, 561 of 638 documents |
| `article_citations` | not scoreable | P 63.6% [60.6–66.6] R 76.8% [73.8–79.6] **F1 69.6%** |
| `statute_citations` | P 97.0% R 69.2% | **P 97.0% R 69.2%** — unchanged |

The last row is the control. A second pass that moved the first pass's numbers
would be a different measurement wearing the same name.

**Why this is not the guess the pack refuses.** `resolve.yaml` says attaching a
bare article to whichever statute was mentioned nearby would manufacture
citations. It would. This does not do that: the statute comes from an edge the
model asserted, so a wrong link is a wrong *extraction*, which
`article_citations` now measures rather than hides. And 261 of the 543 built an
identifier the backbone does not hold — those stay unresolved rather than being
linked to the nearest thing.

Precision is capped at 82.9% here for the same structural reason as oss's
`thread_package`: 802 possible gold pairs against 968 claimed.

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
  engine saying so. `graphpack bench bench-wiki --ingest --hybrid` does both in
  one process and scores the fusion retriever; that comparison is below.
- `nomic-embed-text`, chunk size 1024, overlap 128, top-30 retrieved.
- **Chunks are reduced to articles.** Several chunks of one article are one
  result, taking the rank of its best chunk. Counting chunks would make Hit@10 a
  measure of how finely an article was split.
- **No comparison to the published table is made here**, and the reason is
  worse than it looked. See the next section.

### The full-text half, measured at last

Every benchmark number in this project had been the vector leg alone, and not by
choice: the engine's BM25 docstore lives in memory on the object that ingested,
so a `bench` run in a separate process has vectors and nothing else.
`--ingest --hybrid` does both in one process and scores the fusion retriever —
vector, BM25 and the property graph together.

```bash
uv run graphpack pack reset bench-wiki --extraction-only --yes
uv run graphpack bench bench-wiki --ingest --hybrid
```

Both columns are the metadata-embedded configuration, which is what the pack
carried at the time. The committed pack now hides metadata from the embedding
and scores 0.731 / 0.747 — see
[The same change moves the two metrics in opposite directions](#the-same-change-moves-the-two-metrics-in-opposite-directions).

| | vector only | **hybrid** | change |
|---|---:|---:|---:|
| Hit@1 | 0.631 | 0.631 | **0.000** |
| Hit@2 | 0.794 | **0.872** | +0.078 |
| Hit@4 | 0.909 | **0.953** | +0.044 |
| Hit@10 | 0.977 | **0.992** | +0.015 |
| MRR@10 | 0.759 | **0.782** | +0.023 |

**Hit@1 does not move at all, and everything below it does.** Fusion is not
finding a better first answer; it is pulling more of the right articles into
positions two through four, where a vector-only ranking had them further down.
For a multi-hop benchmark — where a query rests on several articles and the
system needs them all — that is the more useful half of the ranking, and it is
also the half a single-number summary hides.

#### This table is a correction, and the old row is reproducible on demand

It first read Hit@2 0.847, Hit@10 0.987, MRR@10 0.777. **Those numbers were
measured on a corpus that held two copies of every passage**, and this is not an
inference — the old row was reproduced exactly, all five figures, by
deliberately ingesting twice:

| | duplicated (as published) | **clean** |
|---|---:|---:|
| Qdrant points | 17,854 | **8,927** |
| Hit@1 | 0.631 | 0.631 |
| Hit@2 | 0.847 | **0.872** |
| Hit@4 | 0.953 | 0.953 |
| Hit@10 | 0.987 | **0.992** |
| MRR@10 | 0.777 | **0.782** |

The clean run was made twice, from `pack reset` each time, and the two agree to
every printed digit — so the difference is the corpus, not variance.

**Why nothing caught it.** `bench --ingest` refuses to ingest over a populated
pack, precisely so this cannot happen. It counted `:Chunk` nodes in Neo4j. A
pack with `extract: false` never writes any — the corpus goes to Qdrant and the
graph stays empty — so on `bench-wiki`, *the pack the guard was written for*, it
read zero for a full corpus and could not fire. The guard now counts Qdrant
points, and it exists on `ingest` as well as `bench --ingest`.

**What duplication does to a ranking**, since it is not obvious: it does not add
wrong answers, it spends positions. Thirty retrieved chunks reach half as many
distinct articles when every chunk appears twice, so an article that was ranked
fourth is pushed past the cut. That is why Hit@1 is identical — the best chunk
is still the best chunk — and why the loss lands in the middle of the ranking.

**Vector-only was never affected.** A plain `bench` run does not ingest, so it
could not duplicate; the 0.759 / 0.909 / 0.977 row above was re-measured on the
clean corpus and reproduces exactly. Only the `--ingest` path could write a
second copy, and only the hybrid table came from it.

Both legs are scored at the same depth. The vector retriever is built per call
at `--top-k`; the fusion retriever was built during the ingest at whatever depth
the engine chose, so it is set explicitly before scoring. Without that this table
would be comparing depths.

### The comparison, actually run

The section below worked out why the two tables were not comparable. This is
what happened when they were made comparable: same embedding model, same metric,
same retrieval depth.

```bash
# domains/bench-wiki/pack.yaml, temporarily:
#   embedding: {kind: openai, model: text-embedding-ada-002, dimension: 1536}
uv run graphpack ingest bench-wiki
uv run graphpack bench bench-wiki --chunk-level --top-k 20
```

| | **ours, ada-002** | **paper, ada-002** | paper's best (no reranker) |
|---|---:|---:|---:|
| **MRR@10** | **0.417** | **0.4203** | 0.4298 |
| Hits@10 | 0.407 | 0.6381 | 0.6718 |
| Hits@4 | 0.268 | 0.5040 | 0.5221 |

**MRR@10 agrees to three decimal places — 0.417 against 0.4203.** Given the same
embedding model and the same definition, this pipeline reproduces the published
number. That is the single most useful thing the benchmark pack was built to
find out, and it took correcting our own metric first: the same run scored
0.759 the way this project had been reporting it.

For scale, the same corpus and metric on `nomic-embed-text`, the local model
everything else here uses: **MRR@10 0.378**. Between the paper's `llm-embedder`
(0.2558) and its ada-002 (0.4203) — which is where a small open model should sit,
and another sign the measurement is now the right one.

### Where it still disagrees, and why

Evidence recall is well below: 0.407 against 0.638 at depth 10. MRR matching
while recall does not is a specific pattern — we find the *first* piece of
evidence as early as they do, and recover fewer of the rest.

Two hypotheses, and the first was wrong. Chunking or the matcher might impose a
ceiling: an evidence sentence split across a chunk boundary can never be found
by containment. Measured rather than assumed —

```
  981  distinct evidence sentences
8,927  chunks searched
  981  present whole inside the chunked corpus
       ceiling on evidence recall: 100.0%
```

— so no ceiling at all, and the gap is real retrieval difference.

The second holds up. **The paper chunks at 256 tokens; this pack chunks at
1024.** Four times larger, which costs nothing for MRR — a large chunk carrying
the evidence ranks like a small one — and costs a great deal for coverage,
because twenty large chunks reach fewer documents. Measured on 60 queries:

```
  5.3  distinct articles among the top-20 chunks
  2.6  gold articles the average query needs
```

Twenty chunks a quarter the size would span roughly four times as many articles,
which is the shape of the missing recall.

**This prediction was then run, and it is wrong.** See
[The paper's chunk size, and the diagnosis it refutes](#the-papers-chunk-size-and-the-diagnosis-it-refutes)
— 256-token chunks scored *worse*, and the reasoning above turns out to be an
argument about articles reached, which is the wrong quantity for a metric that
counts sentences. The paragraph stays as written because a refuted prediction
that was recorded in advance is worth more than a corrected one.

**What stopped it at the time.** Re-ingesting at 256 tokens failed outright:

```
Metadata length (269) is longer than chunk size (256).
```

This pack attaches title, outlet, category and date to every chunk, and the
splitter charges that against the chunk budget — the behaviour
[ENGINE.md](ENGINE.md) documents, met from the other side. Reaching the paper's
chunk size needs the metadata hidden from the embedding as well, which is a
second change and arguably a *closer* match to the paper (it embedded article
text, not text with a header). It is the obvious next run and it has not been
made.

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

All four were then done, minus the reranker, and
[The comparison, actually run](#the-comparison-actually-run) above is the
result: **MRR@10 0.417 against the paper's 0.4203**. The 0.759 in the table above
is left where it is rather than deleted, because the useful part of this section
is that it was reported as a headline for two phases before anyone read the
paper's definitions.

### The paper's chunk size, and the diagnosis it refutes

Two sections above predicted this run and said what it would show. It was run,
and it showed the opposite.

The prediction, quoted from [Where it still disagrees](#where-it-still-disagrees-and-why):

> The paper chunks at 256 tokens; this pack chunks at 1024. Four times larger,
> which costs nothing for MRR […] and costs a great deal for coverage, because
> twenty large chunks reach fewer documents. […] Twenty chunks a quarter the
> size would span roughly four times as many articles, which is the shape of the
> missing recall.

**Wrong.** Three configurations, same 500 sampled queries, same depth, all local:

| | MRR@10 | evidence recall@10 | chunks |
|---|---:|---:|---:|
| A — 1024, metadata embedded (the published setup) | 0.389 | 0.385 | 8,927 |
| **C — 1024, metadata hidden from the embedding** | **0.466** | **0.456** | 8,927 |
| B — 256, metadata hidden (the paper's size) | 0.354 | 0.318 | 32,877 |

Matching the paper's chunk size made retrieval **worse by every measure**.

**Why the reasoning failed.** It was an argument about *articles reached*, which
is the right quantity for article-level scoring and the wrong one for evidence
recall. Evidence recall counts sentences, not documents, and twenty chunks of
256 tokens are a quarter of the text of twenty chunks of 1024 — a quarter of the
chances to contain anything. The reasoning was carried over from one metric to
the other without being re-derived, which is the same mistake this document
spent a whole section correcting when it published article-level Hit@K under the
paper's name.

**And part of it is a hard ceiling.** F2 measured that every evidence sentence
survives 1024-token chunking whole — 981 of 981, a 100% ceiling. At 256 tokens:

```bash
uv run graphpack bench bench-wiki --ceiling
```
```
895 of 981 evidence sentence(s) survive chunking whole in 32,877 chunk(s)
  — ceiling on evidence recall 91.2%
```

Eighty-six sentences now fall across a boundary and can never be found by
containment. That accounts for about 4 points of the 14-point drop; the other 10
are the volume argument above.

`--ceiling` is a command rather than the throwaway script it was twice, because
this measurement has now refuted two hypotheses and the number turns out to
depend on the configuration in ways nobody predicts. The committed setup — 1024
tokens with metadata hidden, so different boundaries again — reports **99.9%**:
one sentence, not zero. Small, and it would have been invisible without asking.

### The same change moves the two metrics in opposite directions

Before the next section claims hiding metadata is a win, here is the part that
makes it a choice. The article-level numbers were re-measured on the committed
configuration:

| | metadata embedded | **metadata hidden** |
|---|---:|---:|
| chunk level, evidence recall@10 | 0.385 | **0.456** |
| chunk level, MRR@10 | 0.389 | **0.466** |
| article level, vector, MRR@10 | **0.759** | 0.731 |
| article level, hybrid, MRR@10 | **0.782** | 0.747 |
| article level, hybrid, Hit@1 | **0.631** | 0.592 |

**It helps one metric by seven points and costs the other three.** Not a
contradiction — the two measure different things, and the metadata is exactly
the kind of text that separates them. `title`, `source` and `category` identify
the *article* a passage came from, so embedding them pulls a chunk's vector
toward its document, which is what article-level scoring rewards and what
evidence recall does not care about at all. Hide them and each chunk is embedded
for what it says.

**Which is committed, and why.** Metadata hidden. `bench-wiki` exists to be
compared against MultiHop-RAG, whose metric is chunk-level, and this
configuration is the better one for that. The article-level numbers throughout
this document are labelled with which setup produced them, and the cost is
stated here rather than buried in a re-run.

This is also the plainest evidence in this repository for something it has been
asserting since phase 7: **which metric you pick is not a reporting detail.** A
change that would have been written up as a clear improvement under one
definition is a clear regression under another, from the same run.

### Hiding metadata from the embedding is worth more than the chunk size

The row that was supposed to be a control turned out to be the result. A → C
changes one thing — four metadata fields stop being prepended to every chunk
before it is embedded — and it buys **+0.077 MRR and +0.071 evidence recall**,
larger than anything else measured on this pack that is not a reranker.

```yaml
# domains/bench-wiki/sources.yaml, corpus block
hide_from_model: [title, source, category, published_at]
```

**The mechanism is measurable, and it is not the obvious one.** The obvious
reading is dilution — every chunk carried `source: Sporting News`,
`category: sports` and a timestamp, so a template shared across thousands of
chunks was part of what got embedded. That may also be true, and it is not what
the chunk counts say happened.

The splitter is metadata-aware: it subtracts the metadata length from the chunk
budget, so 269 tokens of metadata left 755 tokens for text. Hiding it gives all
1,024 back. The corpus is the same text either way, so the chunk count moves:

| | text tokens per chunk | chunks | evidence recall@10 |
|---|---:|---:|---:|
| 256, metadata hidden | ~256 | 32,877 | 0.318 |
| 1024, metadata embedded | ~755 | 8,927 | 0.385 |
| **1024, metadata hidden** | **~1,024** | **7,327** | **0.456** |

**Evidence recall at fixed K tracks how much real text the K chunks contain**,
monotonically, across all three runs. Twenty chunks retrieve 5,120 / 15,100 /
20,480 tokens of article text respectively, and the recall order is the same.
That single relationship explains both results in this phase — why the paper's
smaller chunk size lost, and why hiding metadata won — and it is the quantity
the original prediction should have been about instead of articles reached.

It also means the win is not free in the way "hide the boilerplate" sounds: part
of it is simply retrieving more text per result, which a larger `top_k` would
also buy.

**And it does not generalise, which is worth measuring before anyone assumes it
does.** The metadata each pack actually embeds, against its own chunk budget:

| pack | metadata tokens/chunk | chunk_size | share of the budget |
|---|---:|---:|---:|
| bench-wiki, before | 269 | 1024 | **26%** |
| bench-wiki, now | 0 | 1024 | 0% |
| tr-law | 31 | 1536 | 2.0% |
| oss | 21 | 1024 | 2.1% |

`bench-wiki` was an outlier by an order of magnitude — a full news headline plus
outlet, category and timestamp on every chunk. The other two spend about a
fiftieth of their budget on metadata, so the same change would buy them close to
nothing, and this is a finding about one pack's configuration rather than a
lesson about GraphRAG.

**Two things worth saying about that one line.** It is the change that unblocked
the paper's chunk size at all: the splitter charges the metadata string against
the chunk budget, so 269 tokens of metadata against a 256-token chunk failed
outright with `Metadata length (269) is longer than chunk size (256)`, which is
where F2 stopped. And it is configuration — the fix for what looked like an
engine limitation was one line of a pack, which is the thesis doing exactly what
it claims.

It was also not obvious. `hide_from_model` reads like a prompt-hygiene knob and
was written as one, for oss's contaminated extraction prompts; it sets
`excluded_embed_metadata_keys` as well, so it is the embedding knob too. On a
pack with `extract: false` — no prompt at all — it is *only* the embedding knob,
and nothing named it that.

**Against the published table**, at last with matched chunk size:

| | MRR@10 | evidence recall / Hits@10 |
|---|---:|---:|
| paper, ada-002, 256 tokens | 0.4203 | **0.6381** |
| ours, nomic, 256 tokens, metadata hidden | 0.354 | 0.318 |
| ours, nomic, 1024 tokens, metadata hidden | **0.466** | 0.456 |

So the recall gap is **not** the chunk size, and after this run it has no
measured explanation. The remaining candidate is the embedding model: F2's
ada-002 run scored 0.407 evidence recall with metadata still embedded, against
nomic's 0.385 in the same configuration. An ada-002 run with metadata hidden has
not been made — it is the obvious next paid run, and it is not being made,
because everything since has been local by choice.

### The reranker, measured

```bash
uv pip install -e '.[rerank]'          # optional extra; nothing else needs it
uv run graphpack bench bench-wiki --chunk-level --top-k 20 --sample 500 --seed 0
uv run graphpack bench bench-wiki --chunk-level --top-k 20 --sample 500 --seed 0 --rerank
```

Same 500 queries both times, same corpus, same depth. The only difference is a
cross-encoder re-ordering what the vector leg returned.

| | rerank off | **rerank on** | change |
|---|---:|---:|---:|
| MRR@10 | 0.466 | **0.700** | **+0.234** |
| evidence recall@10 | 0.456 | **0.597** | +0.141 |
| evidence recall@4 | 0.302 | **0.472** | +0.170 |
| Hit@1 (any) | 0.335 | **0.606** | +0.271 |
| Hit@10 (any) | 0.791 | **0.897** | +0.106 |
| wall clock | ~4 min | **~100 min** | 25× |

Both rows are the committed configuration — 1024-token chunks with metadata
hidden from the embedding. Measured first on the previous setup and re-measured
here when that changed, which is worth a line of its own: the reranker's *gain*
shrank as the baseline improved (+0.271 against a 0.389 baseline, +0.234 against
0.466) while the reranked score itself rose, 0.660 to 0.700. A reranker recovers
what retrieval put within reach; make retrieval better and there is less to
recover and a higher place to recover it to.

**The paper reports +0.193 MRR for its own pair** (voyage-02 0.393 →
bge-reranker-large 0.586). This pipeline gains +0.271 from a nearly identical
starting point — `nomic-embed-text` at 0.389 against their voyage-02 at 0.393 —
using the same reranker they used.

**The over-fetch is not what did it.** A reranked run retrieves 60 and keeps 20;
a plain run retrieves 20. Those are not two variables, because the retriever
returns its 60 in score order, so the first 20 of them *are* the plain top-20.
Widening cannot improve the result on its own; it only supplies candidates the
cross-encoder can promote past the cut, which is the effect being measured.

**Where it lands against the published table**, with the same care F2 needed:

| | MRR@10 | Hits@10 |
|---|---:|---:|
| paper, voyage-02 + bge-reranker-large — their best | 0.5860 | **0.7467** |
| ours, nomic + bge-reranker-large, 500 queries | **0.700** | 0.597 |

The same split as F2, and the same explanation. MRR is above; evidence recall is
below. Our chunks are 1024 tokens against the paper's 256, which costs nothing
for rank-of-first-hit and a great deal for covering an evidence set — twenty
large chunks reach fewer articles. That measurement is
[above](#where-it-still-disagrees-and-why) and it was made before this run, so
this is the prediction holding rather than an explanation found afterwards.

**What it costs.** 25× the wall clock, and that is with the GPU. On this
machine, 60 chunks of ~1,000 tokens take 87.4s on CPU against 22.8s on Apple's
MPS backend — the difference between 55 hours and 14 over the full 2,255-query
set, which is why this measurement is 500 sampled queries and says so.

**Why 500 and not 2,255.** `--sample`, not `--limit`: this project's own rule
forbids file order for anything measured, and `bench` had only `--limit` until
this phase. The first 500 queries are 41.2% comparison questions against 38.0%
overall; a seeded sample of 500 is 39.6%. The baseline row above is a check on
that — 0.389 on the sample against 0.378 measured over the full set.

### The embedding model is not the missing recall either

After chunk size was ruled out, the only candidate left for the gap to the
published evidence recall was the embedding model — and that was the reason to
run a paid ada-002 comparison. A local model two tiers up answers it for nothing:

| | dimensions | MRR@10 | evidence recall@10 |
|---|---:|---:|---:|
| `nomic-embed-text` (committed) | 768 | 0.466 | 0.456 |
| **`mxbai-embed-large`** | 1024 | **0.503** | **0.473** |
| paper, `text-embedding-ada-002` | 1536 | 0.4203 | **0.6381** |

**+0.017 of recall, against 0.165 missing.** Going up a model tier buys about a
tenth of the gap, so a third tier will not close it either. The embedding model
is a real but small effect and it is not the explanation.

Note the split has not moved: our MRR is now *above* the paper's ada-002 while
our recall is a third below it. Two systems agreeing on where the first correct
chunk lands and disagreeing that much on how many of the rest are found is a
strange shape for a retrieval difference, and every retrieval-side explanation
this document has tried — chunk boundaries, chunk size, embedding model — has
now been measured and rejected.

**What is left is the relevance criterion itself.** A chunk counts here when it
*contains* an evidence sentence, matched as an exact substring after whitespace
and case are normalised. If the paper's check is looser than that — fuzzy,
overlap-based, or model-judged — its recall would be higher on identical
retrieval, and no amount of retrieval work on this side would close it. That is
not a hypothesis this repository can test: it needs their matching code, not
another run.

**`mxbai-embed-large` is not committed**, and the reason is the cost of the
number rather than the number. Every figure in this document is
`nomic-embed-text`; adopting a model worth +0.037 MRR would mean re-running
article-level, hybrid, and both reranked configurations to keep them
comparable — hours, for an effect smaller than the reranker's by a factor of
six. It is measured, it is written down here, and switching is two lines of
`pack.yaml` when there is a reason to.

### The reranker helps both metrics; it is not another trade

Hiding metadata improved one measurement and cost the other, which raised the
obvious question about the reranker. It does not do that. Same 500 queries, the
same committed pack, scored both ways:

| | rerank off | **rerank on** | change |
|---|---:|---:|---:|
| chunk level · MRR@10 | 0.466 | **0.700** | +0.234 |
| chunk level · evidence recall@10 | 0.456 | **0.597** | +0.141 |
| article level · MRR@10 | 0.762 | **0.857** | +0.095 |
| article level · Hit@1 | 0.638 | **0.782** | +0.144 |
| article level · Hit@10 | 0.982 | **0.991** | +0.009 |

**Everything moves the same direction, and the size of the move is headroom.**
Article-level scoring was already at 0.982 by Hit@10 before any reranking, so
there was little left to win and it won +0.009 there and +0.144 at rank one.
Chunk level had room and used it.

That is the difference between a reranker and the metadata change. One re-orders
results and can only help whatever is being counted; the other changed what got
embedded, which helped one definition of a hit by making a chunk stand for
itself and hurt the other by making it stand less for its article.

### Fusion and reranking mostly find the same thing

Both improve retrieval, and the open question was whether they add up. Measured
as a 2×2 — same 500 queries, same committed configuration, chunk level, depth 20:

| MRR@10 | rerank off | **rerank on** | reranking buys |
|---|---:|---:|---:|
| vector only | 0.466 | 0.700 | **+0.234** |
| **hybrid (vector + BM25)** | 0.518 | **0.719** | +0.201 |
| *fusion buys* | *+0.052* | *+0.019* | |

| evidence recall@10 | rerank off | **rerank on** | reranking buys |
|---|---:|---:|---:|
| vector only | 0.456 | 0.597 | **+0.141** |
| **hybrid** | 0.546 | **0.612** | +0.066 |
| *fusion buys* | *+0.090* | *+0.015* | |

**They overlap, and the overlap is most of fusion.** Applied alone the two gains
are +0.052 and +0.234; applied together the total is +0.253, not +0.286. On
evidence recall the redundancy is larger still: +0.090 and +0.141 separately,
+0.156 together. Once a cross-encoder is re-reading sixty candidates, the BM25
leg is telling it something it had already worked out.

**But not the same thing at rank one.** Fusion does not move Hit@1 at all —
0.335 either way, the third time this document has measured that and got exactly
zero. The reranker takes it to 0.606. Fusion pulls more of the right passages
into positions two through ten; only the reranker changes which one is first.

**What that means for anyone choosing.** Fusion is nearly free and buys about a
fifth of what a reranker does. A reranker costs 25× the wall clock and buys the
rest — including the only movement anyone has measured at rank one. Running both
buys +0.019 MRR over the reranker alone, which is inside the noise of most
things and is the one combination this table would not bother with.

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

### tr-law, unconfounded: 7.6%

The same command on tr-law returned 5.9% while only 200 of its 1,578 decisions
were ingested, and that number was reported here and explicitly not used: most
of each answer was not in the vector store to be found, so the gap was sampling
rather than structure.

With the whole corpus ingested the confound is gone, and the number moves very
little:

```bash
uv run graphpack ablate tr-law     # all 1,578 decisions indexed
```

| question | intent | answer | recovered |
|---|---|---:|---:|
| citing-6356 | citing_decisions | 62 | 19% |
| cocited-4857 | co_cited_statutes | 26 | 15% |
| citing-4857 | citing_decisions | 61 | 11% |
| citing-6100 | citing_decisions | 61 | 7% |
| articles-4857 | articles_of | 15 | 7% |
| articles-6100 | articles_of | 24 | 4% |
| cited-by-decision | cited_statutes | 11 | **0%** |
| chain-2025 | citation_chain | 10 | **0%** |
| chain-emsal | citation_chain | 1 | **0%** |

**Mean recovery 7.6% at top-30**, over ten questions, against bench-wiki's 26.8%.

So the sampling was worth removing and it was not what made the number low. A
graph answer in this domain is *less* recoverable from text than in the news
corpus, not more — which is the direction that argues for having a graph. The
reason is visible in the table: the citation-chain questions recover **nothing
at all**. "Which decisions does this line of authority rest on" is three hops of
`CITES`, and no single judgment states the chain; retrieval returns passages
about the statute rather than the decisions that cite it.

The comparison with bench-wiki is now between two clean measurements rather than
one clean and one confounded, and they disagree by a factor of three. Both are
lower bounds — name presence is necessary for a reader to assemble an answer,
not sufficient — and neither says whether an end-to-end system *answers* the
question.

## What has not been measured

- ~~**Article-level citations.**~~ Scoreable now, through the edge extraction
  itself claimed: 802 gold edges where there were none, F1 69.6%. See
  [Resolving through a relation extraction claimed](#resolving-through-a-relation-extraction-claimed).
- ~~**oss under a gold generator that fits it.**~~ Done — see [Giving oss its
  documents back](#giving-oss-its-documents-back). It was pure configuration and
  it worked: 24 gold edges to 135, ±13 points to ±6. What it does *not* do is
  make `dependencies` measurable; that task still has 24 edges and still
  supports no conclusion, and the new task answers an easier question.
- ~~**oss's prompt contamination.**~~ Measured and fixed — see [A third of the
  graph was the prompt we wrote](#a-third-of-the-graph-was-the-prompt-we-wrote).
  It was 31.4% of the entities, invisible to conformance checking, and removing
  it improved the dependency task rather than merely shrinking the graph.
- ~~**tr-law's ablation, unconfounded.**~~ Done: the full corpus is ingested and
  the answer is **7.6%**, against bench-wiki's 26.8%. The sampling was worth
  removing and was not what made the number low.
- ~~**Hybrid retrieval.**~~ Run: MRR@10 0.759 -> **0.782**, Hit@4 0.909 ->
  0.953, and Hit@1 unchanged. See [The full-text half, measured at
  last](#the-full-text-half-measured-at-last).
- ~~**Run-to-run variance on bench-wiki.**~~ Measured: `pack reset` and a full
  `--ingest --hybrid` run, twice, agree to every printed digit. That is what
  made the corpus-duplication correction above attributable rather than a
  shrug — a difference that survives two identical runs is not variance.
- **Run-to-run variance on tr-law.** Measured on oss for the first time
  and it mattered: the same configuration twice gave a `dependencies` gold set
  of 66 and then 37. Nothing here says whether tr-law or the benchmark move
  that much, and the intervals throughout this document assume they do not.
- ~~**The published MultiHop-RAG table.**~~ Run. With the paper's embedding
  model and the paper's metric, **MRR@10 0.417 against its 0.4203**. Evidence
  recall still differs and the reason is measured rather than guessed — see
  [The comparison, actually run](#the-comparison-actually-run).
- ~~**The paper's chunk size, 256 tokens.**~~ Run, once the metadata was hidden
  from the embedding. It made retrieval **worse** — evidence recall@10 0.318
  against 1024's 0.456 — and refuted the diagnosis that predicted it. See
  [the section](#the-papers-chunk-size-and-the-diagnosis-it-refutes).
- **Why evidence recall is below the paper's.** Every retrieval-side
  explanation has now been measured and rejected: chunk boundaries, chunk size,
  and the embedding model — `mxbai-embed-large` buys +0.017 against 0.165
  missing. What remains is the relevance criterion itself, and testing that
  needs the paper's matching code rather than another run here.
- ~~**Reranking.**~~ Run: MRR@10 0.466 → **0.700** with `bge-reranker-large`,
  against the +0.193 the paper reports for its own pair. See
  [The reranker, measured](#the-reranker-measured).
- **Reranking on the full 2,255 queries.** Measured on a seeded sample of 500,
  because the full set is 14 hours on this machine's GPU. The intervals are
  ±4 points and the effect is +27, so the conclusion does not depend on it —
  but the number in the table is a sample's.
- ~~**Reranking with hybrid retrieval.**~~ Measured as a 2×2: they overlap.
  Separately +0.052 and +0.234 MRR; together +0.253, not +0.286. See
  [Fusion and reranking mostly find the same thing](#fusion-and-reranking-mostly-find-the-same-thing).
- **`strict_schema` true versus false.** The sweep this phase planned is not
  worth running: on the dynamic extractor the setting is inert, and the section
  above is the evidence.
- **The holdout, for `oss` and `bench-wiki`.** Both still sit at `holdout: 0.0`,
  and the note stands: raise it before error analysis feeds back into
  `aliases.csv` or a normaliser. Nothing in either pack has: `oss`'s two fixes
  were defects — a contaminated prompt and an arbitrary label choice — and
  neither was derived from which gold edges were missed.

  `tr-law` is at 0.3, because it did cross that line and the rule said so. See
  [The holdout this pack's own rule
  demanded](#the-holdout-this-packs-own-rule-demanded).
