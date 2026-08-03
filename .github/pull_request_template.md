<!--
Adding a pack? Most of this does not apply — say which domain and what it cost
(lines of configuration, lines of GraphPack code, lines of engine change), and
skip to the checklist.
-->

## What this changes, and why

<!-- The problem, not the diff. The diff is visible. -->

## How you know it works

<!--
The command you ran and what it printed. If this changes a number in
docs/RESULTS.md, both the old and the new one, plus the hardware and model that
produced them — several figures in this repository are facts about one laptop
and are labelled as such.

If it fixes something silent, say how you made it fail on purpose. A regression
test that passes against the bug is not a regression test.
-->

## Checklist

- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run pytest -m "not integration"`
- [ ] `uv run pytest -m integration` (needs `docker compose -f infra/compose.yaml up -d`)
- [ ] `uv run graphpack packs validate` — all packs, including `_template`
- [ ] `git -C ../flexible-graphrag status --porcelain` prints nothing

## If this touches the contract

- [ ] `domains/_template/` still validates and its comments still describe reality
- [ ] The new capability is general — it is not named after, or conditional on, one pack
- [ ] A pack getting it wrong fails loudly rather than producing an empty result

<!--
That last one is the rule this codebase keeps rediscovering: a wrong number is
worse than an error. An empty gold set, a benchmark scoring 0.000, a corpus of
609 four-character documents — each of those reported success first.
-->
