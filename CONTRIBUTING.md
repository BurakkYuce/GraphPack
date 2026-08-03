# Contributing

Two kinds of contribution, and they are unusually different here.

**Adding a domain costs configuration, not code.** That is the project's claim,
and it is checked mechanically rather than promised. If you want to add a
vertical, you should not need to read this file past the next paragraph.

**Changing GraphPack itself** means changing something general. A capability a
pack needed first is welcome; a special case for one pack is not, and CI will
say so.

## Adding a pack

Copy [`domains/_template/`](domains/_template/) and work down the files in the
order its README gives. Every field is commented, including what goes wrong when
you get it subtly right. `uv run graphpack packs validate <yourpack>` after each
step tells you what is missing.

You should not have to touch anything under `graphpack/`. If you do, that is
worth an issue — either the contract is missing a knob, or the knob exists and
is undocumented, and both are bugs.

## Setting up

```bash
git clone https://github.com/BurakkYuce/GraphPack.git
cd GraphPack

# The engine is a sibling checkout, pinned. Do not modify it — see below.
git clone https://github.com/stevereiner/flexible-graphrag.git ../flexible-graphrag
git -C ../flexible-graphrag checkout 71ce503837475c02cfcfb80cd882e3721fcbe1bc

uv venv --python 3.12
uv pip install -e ../flexible-graphrag/flexible-graphrag
uv pip install -e ".[dev]"

docker compose -f infra/compose.yaml up -d
uv run graphpack migrate
uv run graphpack doctor          # says what is reachable and what is not
```

Always run from the repository root. The engine reads `.env` relative to the
working directory, and picking up its configuration by accident shows up only as
data in the wrong place.

## Before you open a pull request

```bash
uv run ruff check . && uv run ruff format .
uv run pytest -m "not integration"     # ~4s
uv run pytest -m integration           # needs the compose services
uv run graphpack packs validate
git -C ../flexible-graphrag status --porcelain   # must print nothing
```

That is exactly what CI runs, in that order.

## The four rules CI enforces

These are the load-bearing ones. Each is a test rather than a convention.

1. **`graphpack/` never imports `domains/`, and `domains/` contains no `.py`.**
   A pack that can run code is not configuration.
2. **No pack name appears as a string literal in `graphpack/`.** The moment
   `if pack == "oss"` is possible, the abstraction stops being one.
3. **The engine checkout is byte-identical to upstream.** "No code changes" only
   means something with a hard boundary, and `git status --porcelain` on the
   engine is the hardest one available.
4. **Every pack passes `graphpack packs validate`.** Including `_template`,
   which is what keeps it from going stale.

If a change needs one of these relaxed, say so in the pull request and explain
what the replacement check is. Removing a check silently is the one thing that
makes every number in `docs/RESULTS.md` unverifiable.

## Working against the engine

Read [`docs/ENGINE.md`](docs/ENGINE.md) before changing anything that touches
it. It documents the engine behaviours the design depends on — several of them
surprising, several of them silent — and each is pinned by a test so an upstream
change surfaces as a failure rather than as wrong numbers.

The short version: the engine passes no triple constraints to the extractor, so
LlamaIndex validates against its own example schema and discards everything a
real pack produces; `SchemaLLMPathExtractor._aextract` catches the resulting
errors and reports success; BM25 lives in the ingesting process and does not
survive a process boundary; and `USE_ONTOLOGY` is a process-global singleton
that cannot represent two packs. GraphPack works around all of these on its own
side, which is why the pin is a deliberate act — bumping `ENGINE_REF` means
re-running the evaluation and saying which version produced which numbers.

## Style, as it is actually applied here

Ruff settings are in `pyproject.toml`; formatting is not a matter of taste.
What is not mechanical:

**Comments say why, not what.** The code says what it does. A comment earns its
place by recording something the next reader cannot see — a measurement, a
constraint the library imposes, or a mistake that was already made once.

**A wrong number is worse than an error.** This is the design rule the codebase
keeps rediscovering. Every stage should be able to say *which* kind of failure it
had: `graphpack bench` distinguishes "retrieved nothing" from "retrieved and
could not attribute"; `graphpack eval` distinguishes "nothing ingested" from
"nothing resolved" from "nothing co-mentioned". Both distinctions exist because
the undifferentiated version sent somebody to the wrong place for hours.

**Claims in the docs carry the command that produced them.** If you change a
number in `docs/RESULTS.md`, change the command next to it too, and say what
hardware and which model produced it. Several timings in this repository are
facts about one laptop and are labelled as such.

**Report what you measured, including when it refutes you.** Two written
diagnoses in this project were wrong and were replaced by measurements rather
than quietly dropped; both are still in `docs/WRITEUP.md` with what disproved
them. That is the standard.

## Tests

`pytest -m "not integration"` is the fast suite and must stay fast — it builds
throwaway packs on disk rather than leaning on the real ones, so a change to
`domains/oss/` cannot turn a contract test green or red.

Integration tests need the compose services. One thing to know when writing
them: `__Entity__.id` is unique across the *whole database*, not per pack —
Neo4j Community has a single database and packs are separated by a property. A
fixture writing bare ids passes on an empty CI database and fails on any machine
that has actually run an ingest. Prefix fixture ids with the test pack name.

## When you interrupt an ingest

Killing an ingest mid-write can leave a stub node behind — a `Chunk` with an
empty id, no text and **no pack tag**. Nothing routine removes it: `graphpack
pack reset` deletes `(n {pack: $pack})`, and an untagged node matches no pack,
so it survives every reset and then fails the first assertion of every pack's
`checks.cypher` forever.

`graphpack backbone check <pack>` is what finds it, which is the assertion
working as designed. Removing it is a one-off:

```cypher
MATCH (c:Chunk) WHERE c.pack IS NULL AND coalesce(c.id, '') = '' AND NOT (c)--()
DETACH DELETE c
```

Check the degree and the properties before deleting anything — the point of the
assertion is that an untagged node is unattributable, so it is worth looking at
rather than sweeping away.

## Reporting a bug

The most useful report names the stage and what it printed. `graphpack doctor`
output, the command you ran, and the counts you expected against the counts you
got. A number that looks wrong is a good bug report here — most of the real
defects in this repository were found by a count being an order of magnitude off
rather than by anything raising.
