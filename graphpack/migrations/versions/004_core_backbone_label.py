"""Move the shared backbone label from ``Thing`` to ``__Backbone__``.

Migration 003 gave every loaded node a shared ``Thing`` label so the edge merge
could use an index — see that file for why that matters. The name was wrong, and
CI said so within the hour: a pack may legitimately declare a label called
``Thing``, one of the test packs does, and then the per-label uniqueness
constraint cannot be created because this index already exists on it:

    Neo.ClientError.Schema.IndexAlreadyExists:
    There already exists an index (:Thing {pack, id}).
    A constraint cannot be created until the index has been dropped.

That collision is also where the ``Thing`` index found in a live database had
come from. It was test residue, not — as 003's author supposed — an index
somebody had designed and never wired up.

``__Backbone__`` cannot collide: ``load.py`` rejects any pack label that does not
match ``[A-Za-z][A-Za-z0-9_]*``, so no pack can declare a name beginning with an
underscore.

003 is left as it was written rather than edited. It has been applied, and the
runner fingerprints applied migrations precisely so that an edit is caught
instead of silently leaving a graph in a state no version of the code produces.

**Order matters here.** ``Thing`` is removed only from nodes carrying more than
one label — a pack that declares ``Thing`` as its own writes nodes whose only
label is that one, and those must keep it. So the removal runs *before*
``__Backbone__`` is added, when the label count still distinguishes the two.
"""

_DROP_OLD_INDEX = "DROP INDEX graphpack_shared_identity IF EXISTS"

_NEW_INDEX = (
    "CREATE INDEX graphpack_backbone_identity IF NOT EXISTS "
    "FOR (n:`__Backbone__`) ON (n.pack, n.id)"
)

#: Before the new label is added, while `size(labels(n)) > 1` still means "this
#: node has its own label and 003 added Thing to it".
_UNDO_003 = """
MATCH (n:Thing)
WHERE size(labels(n)) > 1
CALL (n) { REMOVE n:Thing } IN TRANSACTIONS OF 10000 ROWS
"""

_BACKFILL = """
MATCH (n)
WHERE n.pack IS NOT NULL
  AND n.id IS NOT NULL
  AND NOT n:`__Backbone__`
  AND NOT n:`__Entity__`
  AND n.text IS NULL
CALL (n) { SET n:`__Backbone__` } IN TRANSACTIONS OF 10000 ROWS
"""

_COUNT = "MATCH (n:`__Backbone__`) RETURN count(n) AS n"


def up(session) -> None:
    session.run(_DROP_OLD_INDEX)
    session.run(_UNDO_003).consume()
    session.run(_NEW_INDEX)
    session.run(_BACKFILL).consume()
    labelled = session.run(_COUNT).single()["n"]
    if labelled:
        import logging

        logging.getLogger(__name__).info("Backbone label applied to %d node(s)", labelled)
