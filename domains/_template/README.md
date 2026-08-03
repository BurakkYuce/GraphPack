# Writing a pack

Copy this directory, rename it, and change `name:` in `pack.yaml` to match. Then
work down the files in this order — each one depends on the one before it, and
`graphpack packs validate <yourpack>` after every step will tell you what is
still missing.

```
domains/yourpack/
├── pack.yaml        identity, engine knobs                        step 1
├── ontology.ttl     what kinds of thing exist                     step 2
├── sources.yaml     where records come from, what they become     step 3
├── resolve.yaml     mention -> canonical id                       step 4
├── eval.yaml        how to grade extraction                       step 5
├── retrieval.yaml   what the graph can be asked                   step 6
├── aliases.csv      surface forms no rule reaches
├── questions.jsonl  QA set for the agent
├── checks.cypher    hand-written sanity queries
└── data/            fetched files — gitignored, plus a MANIFEST.txt
```

Every file is commented with what it does, what each field means, and — where
this project has been bitten — what goes wrong when you get it subtly right.
Read them in that order rather than reading this file further; they carry the
detail and they cannot go stale, because CI validates this pack alongside the
real ones.

## The three steps that actually decide whether it works

**Pick a domain with a structured half and an unstructured half that overlap.**
Metadata that already states what a correct extraction would find is what lets
the corpus grade itself. Without it you can still ingest, retrieve and traverse
— you just cannot score extraction without writing annotations.

**Choose the gold generator by asking whether your documents are nodes.** If
they are, `document_edges`, and every document with one edge contributes gold.
If they are not, `backbone_edges`, and you need two related entities in the same
document — which is much rarer than it sounds. In the `oss` pack that difference
was 24 scoreable edges against 150. `eval.yaml` explains both.

**Keep the corpus id and the document node id the same template.** They are
joined by string equality. A drift of one character produces an empty gold set,
and an empty gold set reports as "0 gold edges" — exactly what a corpus with
genuinely no gold reports, after however long extraction took.

## Running it

```bash
uv run graphpack packs validate yourpack     # static, no services, run it often
uv run graphpack backbone fetch yourpack     # HTTP -> data/*.jsonl
uv run graphpack backbone load  yourpack     # jsonl -> Neo4j
uv run graphpack backbone check yourpack     # your checks.cypher
uv run graphpack ingest   yourpack           # chunk, embed, extract   (the slow one)
uv run graphpack resolve  yourpack           # mentions -> canonical ids
uv run graphpack validate-triples yourpack   # does the graph obey the ontology?
uv run graphpack eval     yourpack           # precision / recall / F1
uv run graphpack ask      yourpack "..."     # traverse and answer
```

`fetch` will fail here: the URL is `example.invalid` on purpose, so that
copying this pack cannot accidentally hit somebody's API. Everything up to it
works, which is what keeps the file honest.

## What `packs validate` does not check

Knowing where the tool stops is part of using it. It will not tell you that

- `checks.cypher` is malformed, or that a `[must be empty]` marker landed on the
  wrong line — that file is never parsed;
- `questions.jsonl` is malformed, or names an intent that does not exist;
- a `pack.yaml` key is misspelled — unknown keys are ignored in silence, so
  `chunck_size` leaves you on the default and says nothing;
- an `aliases.csv` id matches anything in your backbone.

It also cannot check anything that needs data: whether your ids actually join,
whether a normaliser collapses two different things into one identifier,
whether `on_error: skip` quietly halved your corpus. `checks.cypher` and reading
the counts is how you catch those.

## One warning this pack emits on purpose

```
note  no resolve rule for BOOK — mentions of those types stay unlinked
```

That is correct and left in. The ontology declares `Book`, so extraction may
produce `BOOK` mentions — a description that names another book — and this pack
has no rule for them, so they stay unlinked rather than being counted as
resolution failures. Read your own notes the same way: each one is either
something to fix or something to decide is fine, and leaving it undecided is how
a pack ends up measuring its own omissions.
