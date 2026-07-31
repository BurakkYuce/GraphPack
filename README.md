# GraphPack

Declarative domain packs on top of the
[flexible-graphrag](https://github.com/stevereiner/flexible-graphrag) engine.

A **pack** is a directory of configuration — an OWL ontology, some YAML, a CSV
of aliases — that turns a general GraphRAG pipeline into a specific vertical. No
Python. The engine repository is never modified; CI proves it.

The thesis under test: *adding a new vertical costs configuration, not code —
measured with precision/recall/F1 in two domains that share almost nothing.*
`oss` (Python packaging: ready-made backbone, exact entity resolution, English)
and `tr-law` (Turkish case law: backbone built from citations, fuzzy resolution,
Turkish).

**Status:** phase 1 complete. The `oss` backbone is live: 1,000 packages, 2,437
dependency edges, built from configuration alone and reproducible by command.
Phase 2 (corpus ingest and ontology-guided extraction) next.

## Quickstart

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and Docker.

```bash
git clone https://github.com/BurakkYuce/GraphPack.git
cd GraphPack

# The engine is a sibling checkout, pinned so results stay reproducible.
git clone https://github.com/stevereiner/flexible-graphrag.git ../flexible-graphrag
git -C ../flexible-graphrag checkout 71ce503837475c02cfcfb80cd882e3721fcbe1bc

uv venv --python 3.12
uv pip install -e ../flexible-graphrag/flexible-graphrag
uv pip install -e ".[dev]"

docker compose -f infra/compose.yaml up -d
uv run graphpack migrate
uv run graphpack packs validate

# Build the oss backbone: ~3 minutes of downloads, no credentials, no cost.
uv run graphpack backbone fetch oss
uv run graphpack backbone load oss
uv run graphpack backbone check oss
```

Always run from the repository root: the engine reads `.env` relative to the
working directory, and picking up its configuration by accident is a mistake
that only shows up as data in the wrong place.

## Commands

```
graphpack packs list                list packs
graphpack packs validate [PACK]     static checks — no services needed
graphpack packs schema PACK         compiled extraction schema
graphpack migrate [--dry-run]       apply pending migrations
graphpack migrate status            applied vs pending
graphpack pack register PACK        record version + ontology checksum
graphpack pack reset PACK           delete one pack's data, leave others alone
graphpack backbone fetch PACK       download the pack's structured sources
graphpack backbone load PACK        merge them into Neo4j (idempotent)
graphpack backbone check PACK       run the pack's sanity queries
```

`graphpack packs schema oss` shows what the engine will actually extract with:

```
oss v0.1.0 — 6 entity types, 9 relation types, 7 entity props, 9 triple constraints

entities
  PACKAGE  (NAME)
  RELEASE  (VERSION)
  ...
relations
  DEPENDS_ON  PACKAGE -> PACKAGE
  MENTIONS_PACKAGE  ISSUE -> PACKAGE
  ...
```

## Layout

```
graphpack/          all Python — one package, because the engine occupies the
  packs/            top-level namespace (config, main, ingest, process, …)
  backbone/         structured data → Neo4j, no LLM
  migrations/       ordered, idempotent, tracked as (:_Migration) in the graph
  cli.py
domains/            packs — TTL, YAML, CSV, JSONL. No Python.
  oss/
infra/compose.yaml  Neo4j + Qdrant
docs/ENGINE.md      what GraphPack uses from the engine, and why
```

## The rules, and how they are enforced

| Rule | Check |
|---|---|
| Packs contain no Python | `test_domains_contain_no_python` |
| Code never imports `domains/` | `test_code_never_imports_domains` |
| No pack name appears in code | `test_no_pack_name_is_hard_coded_in_code` |
| Nothing shadows an engine module | `test_no_top_level_module_shadows_the_engine` |
| The engine checkout stays untouched | CI: `git status --porcelain` |
| Migrations apply from empty, in order, twice | CI: fresh-apply |

The third one is the thesis in a single assertion. It matches quoted names only,
so a pack called `oss` does not trip on "cross" or "across".

## Writing a pack

```
domains/<name>/
  pack.yaml         identity, extraction knobs, store targets   (required)
  ontology.ttl      OWL classes and object properties           (required)
  sources.yaml      structured sources → backbone               (required)
  checks.cypher     sanity queries, some of them assertions
  resolve.yaml      mention → canonical id rules                (phase 3)
  eval.yaml         gold generator selection                    (phase 4)
  retrieval.yaml    intent → Cypher template                    (phase 6)
```

`sources.yaml` says where records come from and what they become:

```yaml
normalize:
  slug: [lower, {regex_replace: {pattern: "[-_.]+", replace: "-"}}]

fetch:
  - id: packages
    url: "https://pypi.org/pypi/{project}/json"
    for_each: top-packages.jsonl     # one request per row
    keep: {name: info.name, requires_dist: info.requires_dist}

load:
  - source: packages.jsonl
    node: {label: Package, id: "pypi:{name|slug}"}
  - source: packages.jsonl
    explode: requires_dist           # one row per list element, as {value}
    edge: {type: DEPENDS_ON, from: "pypi:{name|slug}", to: "pypi:{value|slug}"}
```

Ids are templates, so the identifier scheme is a pack decision. `{a,b|slug}`
takes the first field that survives normalising. Uniqueness constraints are
derived from the labels a pack declares — no pack ships a migration.

The ontology is read by the engine's OWL parser, which has opinions:

- classes need an explicit `a owl:Class`; `rdfs:Class` alone is invisible
- relations need `a owl:ObjectProperty` **plus** `rdfs:domain` and `rdfs:range`
- local names reach the extractor upper-cased (`Package` → `PACKAGE`)
- `default`, `none` and `sample` are reserved pack names

`graphpack packs validate` catches all of these before an ingest run does.

Note that `rdfs:domain`/`rdfs:range` do **not** constrain extraction: the engine
never forwards triple constraints to the extractor. GraphPack derives them and
enforces them in its own resolution pass. See
[docs/ENGINE.md](docs/ENGINE.md).

## Testing

```bash
uv run pytest -m "not integration"   # no services required
uv run pytest                        # needs docker compose up
```

## License

MIT.
