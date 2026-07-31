"""Pack registry.

Each ingested pack leaves a ``(:_Pack)`` node recording its version and the
checksum of the ontology it was ingested with.  That checksum is the tripwire:
when a pack's ontology changes, the graph still holds entities typed by the old
one, and comparing the two makes the mismatch visible before anyone trusts a
query result.

Registry rows are written by the pack tooling, not here — this migration only
establishes the shape.
"""

STATEMENTS = (
    "CREATE CONSTRAINT graphpack_pack_name IF NOT EXISTS FOR (p:_Pack) REQUIRE p.name IS UNIQUE",
    # Every domain node carries {pack, id}. Neo4j Community has one database,
    # so this property is the only thing keeping packs apart — a node without it
    # is a bug, and phase 1's sanity checks assert the count is zero.
    "CREATE INDEX graphpack_pack_scope IF NOT EXISTS FOR (n:_Pack) ON (n.name)",
)


def up(session) -> None:
    for statement in STATEMENTS:
        session.run(statement)
