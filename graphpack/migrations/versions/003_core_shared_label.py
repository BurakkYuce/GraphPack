"""Give every backbone node the shared ``Thing`` label, and index it.

Why this exists, measured rather than supposed. An edge names its endpoints by
identifier, and a pack's identifiers span every label it declares — `pypi:x` is
a Package, `gh:o/r` a Repository — so the endpoint match in ``backbone/load.py``
could not name a label:

    MATCH (a {pack: $pack, id: row.start})

Every RANGE index in Neo4j is label-scoped, so that match used none of them and
scanned the whole database once per batch. On the oss pack, batches climbed from
15 seconds to nearly two minutes and loading 25,385 dependency edges took half
an hour.

One label shared by every loaded node fixes it. New loads get it from
``_MERGE_NODE``; this migration adds it to graphs that already exist, so nobody
has to re-run the load that was slow in the first place.

**Which nodes.** The same line ``graphpack/reset.py`` already draws between what
the backbone loaded and what an ingest produced: extracted entities carry the
``__Entity__`` label and text chunks carry ``text``. Everything else with a pack
tag is a backbone node. Reusing that predicate rather than listing labels to
exclude means the two cannot drift apart.

Idempotent, and safe to run on an empty database: ``SET n:Thing`` on a node that
already has it is a no-op, and the batching keeps a large graph from being one
transaction.
"""

STATEMENTS = (
    # Created here as well as in `ensure_constraints`, so a database migrated
    # before any pack is loaded still has the index.
    "CREATE INDEX graphpack_shared_identity IF NOT EXISTS FOR (n:Thing) ON (n.pack, n.id)",
)

_BACKFILL = """
MATCH (n)
WHERE n.pack IS NOT NULL
  AND n.id IS NOT NULL
  AND NOT n:Thing
  AND NOT n:`__Entity__`
  AND n.text IS NULL
CALL (n) { SET n:Thing } IN TRANSACTIONS OF 10000 ROWS
"""

_COUNT = "MATCH (n:Thing) RETURN count(n) AS n"


def up(session) -> None:
    for statement in STATEMENTS:
        session.run(statement)
    session.run(_BACKFILL).consume()
    labelled = session.run(_COUNT).single()["n"]
    if labelled:
        import logging

        logging.getLogger(__name__).info("Shared label applied to %d backbone node(s)", labelled)
