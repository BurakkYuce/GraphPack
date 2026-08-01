# Review round

Run at the end of every phase, before its commit. The point is not to re-run the
tests — CI does that. It is to ask the questions a green pipeline cannot answer.

Each phase so far has shipped at least one thing that passed every automated
check and was still wrong: a `--limit` slice that drew 200 documents from 8
repositories out of 114, an identifier completeness test that inspected the
rendered string instead of asking whether the fields were there, a context
window setting that only ever fired on a value it could not yet see. None of
those were caught by tests. All of them were caught by looking.

## 1. Look at the output, not the exit code

- Read a sample of what was actually produced — rows, entities, edges, matches.
  Do the values look like the thing they claim to be?
- Compare against something independent. The urllib3 dependents were recomputed
  from the raw records rather than trusted from the graph, and that is what made
  the number worth publishing.
- Check the distribution, not just the total. 905 repositories found is a good
  number; 490 was also a "good" number until somebody asked which key names were
  being read.

## 2. Ask what the numbers are hiding

- Is the sample representative, or merely the first N of something ordered?
- Does a headline figure average over cases that should be reported apart? A 95%
  resolution rate made of exact matches and one made of fuzzy matches are
  different claims.
- What share of the input produced nothing measurable? For the oss corpus that
  is 85% of documents, which is worth knowing before drawing conclusions from
  the other 15%.

## 3. Re-run the thing that should not change

- Idempotency: run the load, the migration, the resolution twice. The second run
  must create nothing.
- Determinism: the same seed, the same input, the same result.

## 4. Check the claims still hold

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest                                  # needs docker compose up
uv run graphpack packs validate
git -C ../flexible-graphrag status --porcelain  # must be empty
```

The last one is the thesis. Everything else is hygiene.

## 5. Write down what was learned, including the mistakes

Findings about the engine go in [ENGINE.md](ENGINE.md), findings about a domain
in that pack's README. A workaround without its measurement is folklore, and the
next person to touch it — including a later version of whoever wrote it — will
either trust it blindly or undo it.

Deviations from the plan get recorded with the reason. Three so far: constraints
derived from pack declarations instead of per-pack migrations, `RESOLVED_AS`
edges instead of separate `(:Mention)` nodes, and no BigQuery.

The evaluation round found two defects and one thing that only looked like one.
Both defects were upstream of the number being measured — a contaminated prompt
and an arbitrary label choice — which is the usual place for them: by the time a
score is low, whatever made it low happened several stages earlier. The false
lead cost an hour and is written down in [RESULTS.md](RESULTS.md) alongside the
real ones, because a log line that reads like a fault is itself a fault worth
fixing, and the next person to chase it should find the answer rather than the
chase.

It also produced the first number small enough to say nothing: twenty gold
edges, an interval thirteen points wide either side. Reporting it as "F1 22%"
would have been true and useless. What the phase actually established is where
the evaluable signal goes, which is the thing that can be acted on.

A round can find a defect that belongs to an earlier phase, and the visualisation
round did: drawing tr-law's statutes put their titles on screen for the first
time, and two of them were sentence fragments carrying another statute's number.
Nothing in the test suite could have caught it — every test passed, all eleven
questions routed and resolved, and the graph was self-consistently wrong. Making
data visible is itself a check, and it is worth doing before the numbers are
taken rather than after.

## 6. Decide what the next phase inherits

- Is anything half-built that the next phase will assume is finished?
- Did this phase add a knob nobody documented?
- Is there a manual step that was performed by hand and should be a command?
  `pack reset --extraction-only` exists because clearing extraction while
  keeping the backbone had to be done with hand-written Cypher first.
