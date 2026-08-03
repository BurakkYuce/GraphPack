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

## [Unreleased]

### Added
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

### Fixed
- **Regression:** the corpus template check introduced in `b61b736` captured the
  early `return` of `_check_sources` into its loop, so every pack declaring a
  corpus silently skipped the rest of validation for a day. All fifteen tests
  covering that function passed with the bug in place.
- `eval.yaml` may declare `tasks: []`. It is a statement — "this pack has no
  extraction metrics" — and the contract had been collapsing it with a missing
  key. `bench-wiki` had said it for weeks, unheard.
- `document_edges` without `source_label` fell back to a label no pack writes,
  producing an empty gold set that read as "0 gold edges". Rejected at parse
  time now.
- Integration fixtures wrote `__Entity__` nodes with bare ids. That constraint
  is unique across the database, not per pack, so the suite was green on an
  empty CI database and failed on any machine that had run an ingest.
- `graphpack eval` and `graphpack version` were missing from the README's
  command list.

---

## [0.1.0] — 2026-08-02

The first version where the central claim carries a number. Three packs sharing
no code, one of them added after the other two were finished and measured.

### The claim, as measured

- **Third vertical after the fact.** `bench-wiki` cost 297 lines of
  configuration, 8 lines of GraphPack code (one new knob, `extract: false`), and
  0 lines of engine change — the last verified by CI on every push.
- **Retrieval, on a published benchmark.** MultiHop-RAG, all 2,556 queries:
  MRR@10 **0.759**, Hit@1 0.631, Hit@10 0.977, intervals ±2 points. Vector leg
  only; no comparison to the published table is claimed.
- **Extraction, `tr-law`.** F1 **81.4%**, precision 97.2%, recall 70.0%, over
  150 gold edges.
- **Extraction, `oss`.** F1 22.2% over *twenty* gold edges, ±13 — reported, and
  explicitly not offered as a conclusion about anything.
- **Graph against text.** On `bench-wiki`, 26.8% of a traversal's answer is
  recoverable from the top-30 passages; 75% when the answer is eight entities,
  12% when it is fifty-one.

### Added

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
- The actual cause is structural: 69% of `oss` documents mention exactly one
  package, and `backbone_edges` needs two. That is what the unreleased
  `document_edges` work above addresses.
- **`ENGINE.md` said the opposite of what is true** about triple constraints,
  and said an interrupted ingest costs everything when it costs only the graph.
  Both corrected against measurements.

### Known limits

Stated in `docs/WRITEUP.md` rather than deferred: the `oss` measurement is weak
on its own terms; the benchmark is vector-only and unreranked; the ablation
covers one pack; article-level citations score nothing without context-dependent
resolution; and every timing is a fact about one laptop with one 8B model.

[Unreleased]: https://github.com/BurakkYuce/GraphPack/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/BurakkYuce/GraphPack/releases/tag/v0.1.0
