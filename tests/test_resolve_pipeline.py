"""The resolution pass against a real database.

Built on a synthetic graph rather than on a real ingest: extraction takes hours
and its output varies run to run, and neither property belongs in a test. The
shapes here — a mention carrying a version, an import name, something the
backbone has never heard of — are the ones taken from real extraction output.
"""

from __future__ import annotations

import textwrap

import pytest

from graphpack.resolve import load_rules, resolve_pack

pytestmark = [pytest.mark.integration, pytest.mark.graph]

PACK = "_graphpack_resolve_test"

RULES = textwrap.dedent(
    """\
    normalize:
      pkg:
        - strip
        - lower
        - {regex_extract: {pattern: "^([A-Za-z0-9][A-Za-z0-9._-]*)"}}
        - {regex_replace: {pattern: "[-_.]+", replace: "-"}}

    resolve:
      - entity: PACKAGE
        target: Package
        id: "pypi:{name|pkg}"
        match: "{name|pkg}"
        methods: [exact, alias, fuzzy]
        fuzzy_threshold: 93
        on_unresolved: provisional
    """
)

ALIASES = "entity,surface,id\nPACKAGE,PIL,pypi:pillow\n"

#: (mention text, how it should resolve)
MENTIONS = [
    ("urllib3", "exact"),
    ("urllib3 2.0", "exact"),
    ("typing_extensions", "exact"),
    ("PIL", "alias"),
    ("urllib33", "fuzzy"),
    ("something-nobody-publishes", "provisional"),
]


@pytest.fixture
def rules(tmp_path):
    (tmp_path / "resolve.yaml").write_text(RULES, encoding="utf-8")
    (tmp_path / "aliases.csv").write_text(ALIASES, encoding="utf-8")
    return load_rules(tmp_path / "resolve.yaml", tmp_path / "aliases.csv")


@pytest.fixture
def graph(neo4j_session):
    """A backbone and a set of mentions, cleaned up afterwards."""
    neo4j_session.run("MATCH (n {pack: $p}) DETACH DELETE n", p=PACK)
    neo4j_session.run(
        """
        UNWIND $packages AS row
        CREATE (n:Package {pack: $pack, id: row.id, name: row.name})
        """,
        pack=PACK,
        packages=[
            {"id": "pypi:urllib3", "name": "urllib3"},
            {"id": "pypi:requests", "name": "requests"},
            {"id": "pypi:pillow", "name": "Pillow"},
            {"id": "pypi:typing-extensions", "name": "typing-extensions"},
        ],
    )
    # The id is prefixed with the test pack and the mention text is carried on
    # `name`, which is where the pipeline reads it from. `__Entity__.id` is
    # unique across the whole database rather than per pack — Neo4j Community
    # has one database and packs are separated by a property — so a fixture
    # writing the bare text collides with any real ingest that extracted the
    # same name. It passed in CI, where the database is empty, and failed on a
    # machine that had actually run one.
    neo4j_session.run(
        """
        UNWIND $mentions AS row
        CREATE (e:`__Entity__`:PACKAGE {pack: $pack, id: $pack + ':' + row.text, name: row.text})
        """,
        pack=PACK,
        mentions=[{"text": text} for text, _ in MENTIONS],
    )
    yield neo4j_session
    neo4j_session.run("MATCH (n {pack: $p}) DETACH DELETE n", p=PACK)


def _methods_by_mention(session) -> dict[str, str]:
    rows = session.run(
        "MATCH (e:`__Entity__` {pack: $p})-[r:RESOLVED_AS]->(c) "
        "RETURN e.name AS mention, r.method AS method, c.id AS canonical",
        p=PACK,
    )
    return {row["mention"]: row["method"] for row in rows}


def test_each_mention_resolves_by_the_method_it_should(graph, rules):
    """The headline number hides the thing that matters: an exact match is a
    fact and a fuzzy match is a guess."""
    resolve_pack(graph, PACK, rules)

    assert _methods_by_mention(graph) == dict(MENTIONS)


def test_the_report_counts_what_the_graph_holds(graph, rules):
    report = resolve_pack(graph, PACK, rules)

    assert report.total == len(MENTIONS)
    assert report.methods["exact"] == 3
    assert report.methods["alias"] == 1
    assert report.methods["fuzzy"] == 1
    assert report.methods["provisional"] == 1


def test_resolution_is_idempotent(graph, rules):
    """Running twice must not double the edges — the second pass clears the
    first's conclusions before drawing its own."""
    resolve_pack(graph, PACK, rules)
    first = graph.run(
        "MATCH (:`__Entity__` {pack: $p})-[r:RESOLVED_AS]->() RETURN count(r) AS n", p=PACK
    ).single()["n"]

    resolve_pack(graph, PACK, rules)
    second = graph.run(
        "MATCH (:`__Entity__` {pack: $p})-[r:RESOLVED_AS]->() RETURN count(r) AS n", p=PACK
    ).single()["n"]

    assert first == second == len(MENTIONS)


def test_re_resolving_after_an_alias_is_added_changes_the_answer(graph, tmp_path):
    """The whole reason resolution is a pass and not a pipeline step: growing
    the alias table costs seconds, where re-extracting costs hours."""
    (tmp_path / "resolve.yaml").write_text(RULES, encoding="utf-8")
    alias_file = tmp_path / "aliases.csv"

    alias_file.write_text("entity,surface,id\n", encoding="utf-8")
    resolve_pack(graph, PACK, load_rules(tmp_path / "resolve.yaml", alias_file))
    before = _methods_by_mention(graph).get("PIL")

    alias_file.write_text(ALIASES, encoding="utf-8")
    resolve_pack(graph, PACK, load_rules(tmp_path / "resolve.yaml", alias_file))
    after = _methods_by_mention(graph)

    assert before != "alias"
    assert after["PIL"] == "alias"


def test_the_raw_layer_is_left_as_extraction_wrote_it(graph, rules):
    """Resolution records a conclusion beside the mention rather than replacing
    it. Keeping the two distinguishable is what makes a wrong conclusion
    reviewable."""
    resolve_pack(graph, PACK, rules)

    names = {
        row["name"]
        for row in graph.run("MATCH (e:`__Entity__` {pack: $p}) RETURN e.name AS name", p=PACK)
    }
    assert names == {text for text, _ in MENTIONS}


def test_unresolvable_mentions_become_provisional_nodes(graph, rules):
    """A mention nobody can place is still evidence, and the set of them is
    where the next alias entries come from."""
    resolve_pack(graph, PACK, rules)

    row = graph.run(
        "MATCH (e:`__Entity__` {pack: $p})-[:RESOLVED_AS]->(prov:Provisional) "
        "RETURN prov.id AS id, prov.text AS text",
        p=PACK,
    ).single()

    assert row["text"] == "something-nobody-publishes"
    # Namespaced so a provisional node can never be read as a canonical one.
    assert row["id"].startswith(f"prov:{PACK}:package:")


def test_dropping_leaves_no_node_behind(graph, tmp_path):
    rules_yaml = RULES.replace("on_unresolved: provisional", "on_unresolved: drop")
    (tmp_path / "resolve.yaml").write_text(rules_yaml, encoding="utf-8")
    (tmp_path / "aliases.csv").write_text(ALIASES, encoding="utf-8")

    report = resolve_pack(
        graph, PACK, load_rules(tmp_path / "resolve.yaml", tmp_path / "aliases.csv")
    )

    assert report.methods["drop"] == 1
    provisional = graph.run(
        "MATCH (n:Provisional {pack: $p}) RETURN count(n) AS n", p=PACK
    ).single()["n"]
    assert provisional == 0


def test_mentions_of_a_type_no_rule_covers_are_left_alone(graph, rules):
    """Not every extracted type has a canonical form. Counting those as drops
    would make the resolution rate depend on how much the ontology covers."""
    graph.run(
        "CREATE (e:`__Entity__`:CONCEPT "
        "{pack: $p, id: $p + ':backpressure', name: 'backpressure'})",
        p=PACK,
    )

    report = resolve_pack(graph, PACK, rules)

    assert report.total == len(MENTIONS)
    assert "CONCEPT" not in report.by_entity


# ----------------------------------------------------------------------
# A mention with more than one type
# ----------------------------------------------------------------------


def test_a_mention_typed_twice_resolves_under_whichever_fits_best():
    """The store MERGEs __Entity__ nodes globally on id, so one node ends up
    carrying every type the model ever gave it — `requests` was labelled both
    REPOSITORY and PACKAGE. Resolution used to take labels[0], which is Neo4j's
    ordering and not anybody's decision: 24 package mentions became repositories
    that way."""
    from graphpack.resolve.pipeline import _best_across_types

    rules = _rules_for_two_types()
    index = _index_holding({"Package": {"pypi:requests"}, "Repository": set()})

    entity, rule, match = _best_across_types(["REPOSITORY", "PACKAGE"], "requests", rules, index)

    assert entity == "PACKAGE"
    assert match is not None and match.canonical_id == "pypi:requests"
    assert rule.entity == "PACKAGE"


def test_the_stronger_method_wins_over_declaration_order():
    """An exact hit is better evidence of what a mention is than a fuzzy one,
    whichever rule the pack happened to write first."""
    from graphpack.resolve.pipeline import _best_across_types

    rules = _rules_for_two_types()
    index = _index_holding({"Package": {"pypi:requests"}, "Repository": {"gh:psf/requests"}})

    entity, _, match = _best_across_types(["REPOSITORY", "PACKAGE"], "requests", rules, index)

    assert match.method == "exact"
    assert entity in {"PACKAGE", "REPOSITORY"}


def test_a_mention_nothing_resolves_still_reports_a_type():
    """It has to be counted somewhere, and dropped under a type is information;
    dropped under no type is a mention that silently vanishes."""
    from graphpack.resolve.pipeline import _best_across_types

    rules = _rules_for_two_types()
    index = _index_holding({"Package": set(), "Repository": set()})

    entity, rule, match = _best_across_types(["PACKAGE"], "nowhere", rules, index)

    assert match is None
    assert entity == "PACKAGE" and rule is not None


def _rules_for_two_types():
    """Two rules over one mention: PACKAGE declared after REPOSITORY on purpose,
    so declaration order alone would pick the wrong one."""
    import tempfile
    import textwrap
    from pathlib import Path

    from graphpack.resolve.contract import load_rules

    body = textwrap.dedent(
        """\
        normalize:
          slug: [strip, lower]
        resolve:
          - entity: REPOSITORY
            target: Repository
            id: "gh:psf/{name|slug}"
            match: "{name|slug}"
            methods: [exact]
            on_unresolved: drop
          - entity: PACKAGE
            target: Package
            id: "pypi:{name|slug}"
            match: "{name|slug}"
            methods: [exact]
            on_unresolved: drop
        """
    )
    directory = Path(tempfile.mkdtemp())
    (directory / "resolve.yaml").write_text(body, encoding="utf-8")
    return load_rules(directory / "resolve.yaml")


class _FakeIndex:
    def __init__(self, ids):
        self._ids = ids
        self.aliases = {}

    def has(self, label, identifier):
        return identifier in self._ids.get(label, set())

    def match_forms(self, label):
        return {}


def _index_holding(ids):
    return _FakeIndex(ids)


# ----------------------------------------------------------------------
# Resolving through a relation extraction claimed
# ----------------------------------------------------------------------

CONTEXT_RULES = textwrap.dedent(
    """\
    normalize:
      number:
        - strip
        - {regex_extract: {pattern: "(\\\\d+)"}}

    resolve:
      - entity: BOOK
        target: Book
        id: "book:{name|number}"
        methods: [exact]
        on_unresolved: drop

      - entity: CHAPTER
        target: Chapter
        id: "chapter:{name|number}"
        methods: [exact]
        context:
          via: HAS_CHAPTER
          from: BOOK
          id: "chapter:{source|number}/{name|number}"
        on_unresolved: drop
    """
)


@pytest.fixture
def context_rules(tmp_path):
    (tmp_path / "resolve.yaml").write_text(CONTEXT_RULES, encoding="utf-8")
    return load_rules(tmp_path / "resolve.yaml")


@pytest.fixture
def context_graph(neo4j_session):
    """A backbone of chapters that only a book identifies, and mentions of them.

    `chapter:900/3` is deliberately absent from the backbone: the model claims
    it, the identifier is well formed, and it must still not be linked. A
    context pass that resolved it would be manufacturing a citation, which is
    what the tr-law pack refuses in writing.
    """
    neo4j_session.run("MATCH (n {pack: $p}) DETACH DELETE n", p=PACK)
    neo4j_session.run(
        """
        CREATE (:Book {pack: $pack, id: 'book:42', name: '42'})
        CREATE (:Chapter {pack: $pack, id: 'chapter:42/3', name: '3'})
        CREATE (b:`__Entity__`:BOOK {pack: $pack, id: $pack + ':b', name: '42'})
        CREATE (c:`__Entity__`:CHAPTER {pack: $pack, id: $pack + ':c', name: 'chapter 3'})
        CREATE (b)-[:HAS_CHAPTER]->(c)
        CREATE (x:`__Entity__`:BOOK {pack: $pack, id: $pack + ':x', name: '900'})
        CREATE (y:`__Entity__`:CHAPTER {pack: $pack, id: $pack + ':y', name: 'chapter 3'})
        CREATE (x)-[:HAS_CHAPTER]->(y)
        """,
        pack=PACK,
    )
    yield neo4j_session
    neo4j_session.run("MATCH (n {pack: $p}) DETACH DELETE n", p=PACK)


def test_a_mention_that_identifies_nothing_alone_resolves_through_its_edge(
    context_graph, context_rules
):
    """ "chapter 3" is chapter 3 of *what*. The book is not in the text of the
    mention; it is on the far end of a relation extraction produced, which makes
    following it evidence rather than a guess about what was nearby."""
    resolve_pack(context_graph, PACK, context_rules)

    linked = context_graph.run(
        "MATCH (e:`__Entity__`:CHAPTER {pack: $p})-[r:RESOLVED_AS]->(c) "
        "RETURN c.id AS canonical, r.method AS method",
        p=PACK,
    ).data()

    assert linked == [{"canonical": "chapter:42/3", "method": "context"}]


def test_an_identifier_the_backbone_does_not_hold_is_left_unresolved(context_graph, context_rules):
    """The second mention builds `chapter:900/3`, which is well formed and does
    not exist. Linking it to the nearest thing would manufacture a fact; the
    pass leaves it alone and the report counts it as a miss."""
    report = resolve_pack(context_graph, PACK, context_rules)

    assert report.methods["context"] == 1
    assert not context_graph.run(
        "MATCH (:`__Entity__` {pack: $p})-[:RESOLVED_AS]->(c) "
        "WHERE c.id = 'chapter:900/3' RETURN c",
        p=PACK,
    ).data()


def test_the_context_pass_does_not_touch_what_the_methods_already_resolved(
    context_graph, context_rules
):
    """The control. Adding a second pass must not move a number the first pass
    produced — in tr-law, `statute_citations` scored 97.0/69.2 before and after."""
    resolve_pack(context_graph, PACK, context_rules)

    books = context_graph.run(
        "MATCH (e:`__Entity__`:BOOK {pack: $p})-[r:RESOLVED_AS]->(c) "
        "RETURN c.id AS canonical, r.method AS method ORDER BY c.id",
        p=PACK,
    ).data()

    assert books == [{"canonical": "book:42", "method": "exact"}]
