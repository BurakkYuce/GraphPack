# Security

## Reporting a vulnerability

Use GitHub's [private vulnerability
reporting](https://github.com/BurakkYuce/GraphPack/security/advisories/new) —
Security → Report a vulnerability on this repository. Please do not open a
public issue for something exploitable.

Include what you ran, what happened, and what you expected. A proof of concept
helps; a working exploit is not required.

This is a research project maintained by one person, so there is no service-level
commitment. Expect an acknowledgement rather than a patch schedule.

## What this project handles that is worth being careful with

GraphPack is a command-line tool that reads configuration you write, fetches
URLs you name, and talks to databases you run. It is not a network service and
has no authentication of its own. The exposure is therefore mostly in what a
pack can be made to do.

**Credentials live in `.env` and nowhere else.** `GEMINI_API_KEY`,
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GITHUB_TOKEN`, `NEO4J_PASSWORD`. That
file is gitignored. A pack's `fetch` block may reference environment variables
in headers as `${VAR}`, which is how a token reaches a request without being
written down — check that a pack you did not write does not send yours somewhere
you did not intend.

**A pack fetches arbitrary URLs.** `sources.yaml` names them and
`graphpack backbone fetch` requests them. Treat a pack from someone else the way
you would treat any script that makes network calls on your behalf: read its
`fetch` block first.

**A pack's Cypher runs against your database.** `retrieval.yaml` carries query
templates. Interpolation is restricted to `{hops}` and `{limit}`; everything
else — the pack, the entity — is a bound `$parameter` that the driver escapes,
and `packs validate` rejects a template using any other placeholder. That
prevents question text from becoming query text. It does not sandbox the query
itself: a pack you install can read, and in principle write, anything in the
database it runs against.

**Extracted text reaches a model.** Whatever your corpus contains is sent to
whichever provider `.env` configures, including any metadata the pack attaches —
LlamaIndex prepends metadata to chunk text before the model sees it. If your
corpus is sensitive, use a local provider, and use `hide_from_model` for fields
that should not be in a prompt.

**The engine checkout is not audited by this project.** GraphPack pins
`stevereiner/flexible-graphrag` at one commit and never modifies it. Its
dependencies are its own; the pin exists for reproducibility, not as a security
review.

## Out of scope

Vulnerabilities in Neo4j, Qdrant, the engine, or a model provider belong to
those projects. Reports about running GraphPack with credentials committed to a
repository, or with a database exposed to the internet without authentication,
are configuration rather than defects — though a pull request making either
harder to do by accident is welcome.
