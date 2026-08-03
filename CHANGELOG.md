# Changelog

Notable changes, in the order they happened. Dates are the day work landed.

This project's output is measured claims, so a release note that says only what
was added is not much use. Where a version changed a number, the number is here;
where a measurement refuted something this repository had already written down,
that is here too, because it is the more useful half.

Format loosely follows [Keep a Changelog](https://keepachangelog.com/1.1.0/).
Versions are [semantic](https://semver.org/) over the **pack contract** — the
YAML, OWL, CSV and JSONL a pack is written in — not over the Python, which has
no public API.

## [0.1.0] — 2026-08-03

The first tagged version. Everything below happened before it, so this entry is
a starting point rather than a delta — the sections are what a reader arriving
at the tag should know about what it does and what it has shown.

### The claim, as measured

Three packs sharing no code, one of them added after the other two were finished
and measured. `bench-wiki` cost 297 lines of configuration, 8 lines of GraphPack
code (one new knob, `extract: false`) and **0 lines of engine change**, the last
verified by CI on every push.

| | |
|---|---|
| retrieval, MultiHop-RAG, 2,556 queries | MRR@10 **0.777** hybrid, 0.759 vector; Hit@4 0.953, Hit@10 0.987 |
| extraction, `tr-law` — `statute_citations` | F1 **80.8%**, precision 97.0%, over 1,242 gold edges from 711 documents, ±1.2 points |
| extraction, `tr-law` — `article_citations` | F1 **69.6%** over 802 gold edges — scoreable only since resolution learned to follow an extracted edge |
| extraction, `oss` — `thread_package` | F1 **57.5%**, recall 86.2%, over 94 gold edges |
| extraction, `oss` — `dependencies` | F1 21.1% over 37 gold edges, ±13 and not offered as a conclusion |
| graph against text | 26.8% recoverable on news, **7.6%** on case law — both clean |

### Added — making a domain measurable, and making the project handover-ready
- `domains/_template/` — a complete working pack for a made-up domain, every
  field commented. Validated in CI alongside the real packs, so it fails when
  the contract changes instead of going stale.
- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue and pull-request
  templates. The issue set includes a *pack contract gap* report: the one this
  project most wants, for when adding a domain turns out to need Python.
- `oss` scores a second task, `thread_package`, using `document_edges` over new
  `Issue` nodes. The pack's documents are now graph nodes, which is what the
  `tr-law` pack had and this one did not.
- `packs validate` covers `retrieval.yaml`, which nothing parsed until a
  question was asked, and `eval.yaml`, whose errors were being swallowed. It
  also checks that a `document_edges` task's document nodes and its ingested
  documents share an id template, because they are joined by string equality and
  a drift of one character produces an empty gold set rather than an error.

- `Scores.precision_ceiling`, and `graphpack eval` prints it when it binds. A
  gold set narrower than what extraction can claim caps precision below 100%,
  and the bare number then reads as an error rate rather than as headroom.
- `graphpack bench --ingest --hybrid` — ingest and benchmark in one process, so
  the engine's in-memory BM25 leg exists to be scored. The first hybrid number
  this project has had.
- `context:` on a resolve rule: identify a mention through a relation extraction
  itself claimed. "371. maddesinde" is an article number and 371 of *which*
  statute is the question; the model's own `HAS_ARTICLE` edge answers it, which
  is evidence rather than the proximity guess the pack refuses in writing.
- `hide_from_model` applied to the `oss` corpus. It had existed unused so the
  committed configuration would match the published numbers; a re-run now costs
  four minutes rather than ten hours, so that reason expired.

### Measured, and it changed what the documentation claims
- **oss became measurable.** Giving its threads `Issue` nodes and scoring them
  with `document_edges` took the gold set from 24 edges to 94–135 and the
  interval from ±13 points to ±6. Pure configuration: no GraphPack code, no
  engine change, and no re-extraction — it scores the same run.
- **A third of oss's graph was our own prompt.** 154 of 490 entities were named
  by the `url:` line the pack attached, all typed `ISSUE`, `PACKAGE` or
  `REPOSITORY`, so `validate-triples` reported 100% conforming throughout.
  Hiding the field took it to 0.7% and *improved* the graph: repository edges
  10 → 85, dependency F1 14.6% → 21.1%.
- **The new task leaks about ten points.** Its gold comes from the repository
  slug, which the model can see. Hiding the slug costs 86.2% → 74.8% recall, so
  roughly three quarters of it is reading the thread. Measured rather than
  argued, and `repo` stays visible because it is document content.
- **Scores move between identical runs.** Never checked before. The same
  configuration twice gave the same `thread_package` gold set (94, 94) and a
  `dependencies` gold set that nearly halved (66, 37) — so the Wilson interval
  on the second task understates its real uncertainty.

### Fixed
- `graphpack inspect PACK` scoped nothing but the ontology it compared against.
  With two packs ingested it reported 28% conformance (464/1,639) while
  `validate-triples` reported 100% on the same graph — 464 was one pack's,
  1,639 was every pack's. The pack-tag census stays global on purpose.
- **Regression:** the corpus template check introduced in `b61b736` captured the
  early `return` of `_check_sources` into its loop, so every pack declaring a
  corpus silently skipped the rest of validation for a day. All fifteen tests
  covering that function passed with the bug in place.
- `eval.yaml` may declare `tasks: []`. It is a statement — "this pack has no
  extraction metrics" — and the contract had been collapsing it with a missing
  key. `bench-wiki` had been saying it since it was written, unheard.
- `document_edges` without `source_label` fell back to a label no pack writes,
  producing an empty gold set that read as "0 gold edges". Rejected at parse
  time now.
- Integration fixtures wrote `__Entity__` nodes with bare ids. That constraint
  is unique across the database, not per pack, so the suite was green on an
  empty CI database and failed on any machine that had run an ingest.
- `graphpack eval` and `graphpack version` were missing from the README's
  command list.

### How it was built

Eight phases, each with its own review round.

- **Phase 0** — the pack contract, ontology compiler, migrations, CLI.
- **Phase 1** — the `oss` backbone: 1,000 packages, 2,437 dependency edges, no
  model involved.
- **Phase 2** — corpus ingest and ontology-guided extraction.
- **Phase 3** — resolution as a separate pass. Rules change often and extraction
  takes hours; coupling them means every rule change costs another run.
- **Phase 4** — self-labelling evaluation. The corpus carries its own ground
  truth, so nobody annotates anything.
- **Phase 5** — `tr-law`, a second vertical, with no change to shared code.
- **Phase 6** — multi-hop question answering, with a trace of how the answer was
  reached.
- **Phase 7** — `bench-wiki`, and `graphpack bench` for Hit@k and MRR@10.
- **Phase 8** — `graphpack viz`, which writes a run as a self-contained page.

### Fixed — the defects worth naming

Every one of these produced a plausible number before it produced an error.

- **The extractor validated against somebody else's ontology.** The engine
  passes no `kg_validation_schema`, so LlamaIndex fell back to its
  PRODUCT / MARKET example and `strict=True` discarded every triple a real pack
  produced. Turkish case law was being filtered against a schema about consumer
  products, and the ingest reported success. Installing the pack's own
  constraints moved ontology conformance from **17.8% to 100%**.
- **`asyncio.run` per query** closed the loop the engine's clients had bound to.
  Every query after the first failed and the benchmark reported exactly 0.000.
- **`system.search` returns no document identity** — its `source` field is the
  retriever's name, so 8,927 chunks were all attributed to an article called
  "Qdrant vector". Non-empty, so the unattributed counter never fired.
- **`text: body`** is a template with no placeholder: 609 documents of four
  characters, embedded without complaint.
- **`[must be empty]` on the second comment line** silently became commentary,
  and the check suite reported OK because it had stopped looking.
- **Our own bookkeeping was extracted.** LlamaIndex prepends metadata to a
  node's text before the model sees it, so `pack: oss` became a `PACKAGE` entity.
- **Neo4j's label order chose entity types.** `__Entity__` nodes MERGE globally
  on id, so one node accumulates every type the model ever gave it, and
  resolution was taking `labels(e)[0]`.
- **Google's Developer API rejects a schema carrying properties**, and
  `llama_index.llms.google_genai` calls `asyncio.run` inside its synchronous
  `_chat`. Both were invisible because `SchemaLLMPathExtractor._aextract`
  catches `ValueError`, `TypeError` and `AttributeError` and reports no triplets.

### Changed — by measurement, against what had been written

- **A written diagnosis was refuted.** The `tr-law`/`oss` gap had been
  attributed to model, ontology enforcement and extractor. A controlled re-run
  on `tr-law`'s exact setup changed the graph's quality and not its score:
  precision identical to the decimal, recall halved, F1 22.2% → 15.4%.
- **And so was its replacement.** The remaining cause was then written down as
  backbone coverage. Widening the backbone eight-fold — 1,000 to 8,000 packages,
  2,437 to 25,367 edges — moved the gold set from 22 edges to 24.
- **The third diagnosis held.** The actual cause is structural: 69% of `oss`
  documents mention exactly one package, and `backbone_edges` needs two. Written
  down before it was run, then run — see the `document_edges` work above.
- **`ENGINE.md` said the opposite of what is true** about triple constraints,
  and said an interrupted ingest costs everything when it costs only the graph.
  Both corrected against measurements.

### Known limits

Stated in `docs/WRITEUP.md` rather than deferred: `oss`'s dependency task still
supports no conclusion and its measurable task asks an easier question and leaks
about ten points; the benchmark is vector-only and unreranked; hybrid retrieval
has no number at all, because the engine's BM25 leg does not survive a process
boundary; article-level citations score nothing without context-dependent
resolution; and every local timing is a fact about one laptop with one 8B model.

[0.1.0]: https://github.com/BurakkYuce/GraphPack/releases/tag/v0.1.0
