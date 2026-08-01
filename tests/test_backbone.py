"""The sources.yaml contract and the loader.

The loader's promise is that reloading unchanged data changes nothing. Anything
weaker makes the backbone useless as the ground truth phase 4 measures
extraction against, so the idempotency case is an integration test against a
real database rather than a mock.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from graphpack.backbone.load import LoadError, ensure_constraints, load_backbone
from graphpack.backbone.sources import SourcesError, load_sources

SOURCES = textwrap.dedent(
    """\
    normalize:
      slug:
        - lower
        - {regex_replace: {pattern: "[-_.]+", replace: "-"}}

    fetch:
      - id: things
        url: https://example.invalid/things.json
        out: things.jsonl

    load:
      - source: things.jsonl
        node:
          label: Thing
          id: "t:{name|slug}"
          properties:
            name: "{name}"
      - source: things.jsonl
        explode: needs
        edge:
          type: NEEDS
          from: "t:{name|slug}"
          to: "t:{value|slug}"
    """
)

ROWS = [
    {"name": "Alpha", "needs": ["beta"]},
    {"name": "beta", "needs": []},
    {"name": "gamma", "needs": ["alpha", "nowhere"]},
]


@pytest.fixture
def pack_data(tmp_path):
    """A sources.yaml and its data, ready to load."""

    def _make(sources_yaml: str = SOURCES, rows: list[dict] = None):
        (tmp_path / "sources.yaml").write_text(sources_yaml, encoding="utf-8")
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)
        (data_dir / "things.jsonl").write_text(
            "\n".join(json.dumps(r) for r in (ROWS if rows is None else rows)) + "\n",
            encoding="utf-8",
        )
        return load_sources(tmp_path / "sources.yaml"), data_dir

    return _make


# ----------------------------------------------------------------------
# Contract
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_parses_fetch_and_load_blocks(pack_data):
    sources, _ = pack_data()

    assert [s.id for s in sources.fetch] == ["things"]
    assert sources.node_labels == ["Thing"]
    assert sources.load[1].edge.type == "NEEDS"


@pytest.mark.unit
def test_a_step_declares_either_a_node_or_an_edge(tmp_path):
    (tmp_path / "sources.yaml").write_text(
        "load:\n  - source: x.jsonl\n    node: {label: A, id: 'a:{x}'}\n"
        "    edge: {type: B, from: 'a:{x}', to: 'a:{y}'}\n",
        encoding="utf-8",
    )

    with pytest.raises(SourcesError, match="exactly one of 'node' or 'edge'"):
        load_sources(tmp_path / "sources.yaml")


@pytest.mark.unit
def test_undefined_normalize_pipeline_is_caught_before_loading(tmp_path):
    """Otherwise ids are built from unnormalised values and the load looks fine."""
    (tmp_path / "sources.yaml").write_text(
        "load:\n  - source: x.jsonl\n    node: {label: A, id: 'a:{x|missing}'}\n",
        encoding="utf-8",
    )

    with pytest.raises(SourcesError, match="undefined normalize pipeline"):
        load_sources(tmp_path / "sources.yaml")


@pytest.mark.unit
def test_a_load_step_reading_an_unfetched_file_is_caught(tmp_path):
    """A typo here produces an empty graph and a successful-looking run."""
    (tmp_path / "sources.yaml").write_text(
        textwrap.dedent(
            """\
            fetch:
              - {id: a, url: https://example.invalid/a, out: a.jsonl}
            load:
              - source: typo.jsonl
                node: {label: A, id: "a:{x}"}
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(SourcesError, match="nothing before it produces"):
        load_sources(tmp_path / "sources.yaml")


@pytest.mark.unit
def test_the_real_pack_parses():
    from graphpack.packs import load_pack

    sources = load_sources(load_pack("oss").path("sources.yaml"))

    assert sources.fetch and sources.load
    assert "Package" in sources.node_labels


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.graph
def test_load_is_idempotent(neo4j_session, pack_data):
    """Reloading unchanged data must write the same rows and create nothing."""
    sources, data_dir = pack_data()
    pack = "_graphpack_test"
    neo4j_session.run("MATCH (n {pack: $p}) DETACH DELETE n", p=pack)

    try:
        first = load_backbone(neo4j_session, pack, sources, data_dir)
        second = load_backbone(neo4j_session, pack, sources, data_dir)

        assert first.total_created > 0
        assert second.total_created == 0
        assert second.written == first.written

        nodes = neo4j_session.run("MATCH (n {pack: $p}) RETURN count(n) AS n", p=pack).single()["n"]
        assert nodes == 3
    finally:
        neo4j_session.run("MATCH (n {pack: $p}) DETACH DELETE n", p=pack)


@pytest.mark.integration
@pytest.mark.graph
def test_edges_to_unknown_endpoints_are_dropped_not_invented(neo4j_session, pack_data):
    """'gamma needs nowhere' must not conjure a `nowhere` node.

    A graph that invents endpoints cannot be used to decide whether a missing
    edge is a real absence, which is the whole point of having a backbone.
    """
    sources, data_dir = pack_data()
    pack = "_graphpack_test"
    neo4j_session.run("MATCH (n {pack: $p}) DETACH DELETE n", p=pack)

    try:
        report = load_backbone(neo4j_session, pack, sources, data_dir)

        assert report.outside["edge:NEEDS"] == 1
        missing = neo4j_session.run(
            "MATCH (n {pack: $p, id: 't:nowhere'}) RETURN count(n) AS n", p=pack
        ).single()["n"]
        assert missing == 0
        edges = neo4j_session.run(
            "MATCH ({pack: $p})-[r:NEEDS]->({pack: $p}) RETURN count(r) AS n", p=pack
        ).single()["n"]
        assert edges == 2
    finally:
        neo4j_session.run("MATCH (n {pack: $p}) DETACH DELETE n", p=pack)


@pytest.mark.integration
@pytest.mark.graph
def test_packs_sharing_the_database_stay_separate(neo4j_session, pack_data):
    """Neo4j Community has one database; `pack` is the only separation there is."""
    sources, data_dir = pack_data()
    neo4j_session.run("MATCH (n) WHERE n.pack STARTS WITH '_graphpack_test' DETACH DELETE n")

    try:
        load_backbone(neo4j_session, "_graphpack_test_a", sources, data_dir)
        load_backbone(neo4j_session, "_graphpack_test_b", sources, data_dir)

        crossing = neo4j_session.run(
            "MATCH ({pack: '_graphpack_test_a'})-[r]->({pack: '_graphpack_test_b'}) "
            "RETURN count(r) AS n"
        ).single()["n"]
        assert crossing == 0
    finally:
        neo4j_session.run("MATCH (n) WHERE n.pack STARTS WITH '_graphpack_test' DETACH DELETE n")


@pytest.mark.integration
@pytest.mark.graph
def test_constraints_are_derived_from_the_declared_labels(neo4j_session, pack_data):
    """No pack ships a migration file; its labels imply its constraints."""
    sources, _ = pack_data()

    ensure_constraints(neo4j_session, sources)

    constraints = [dict(r) for r in neo4j_session.run("SHOW CONSTRAINTS")]
    identity = [c for c in constraints if c["name"] == "graphpack_thing_identity"]
    assert identity, "expected a (pack, id) uniqueness constraint for label Thing"
    assert set(identity[0]["properties"]) == {"pack", "id"}


@pytest.mark.integration
@pytest.mark.graph
def test_a_label_that_is_not_an_identifier_is_refused(neo4j_session, tmp_path):
    """Labels are interpolated into Cypher, so they are checked, not escaped."""
    (tmp_path / "sources.yaml").write_text(
        'load:\n  - source: x.jsonl\n    node: {label: "Bad Label", id: "a:{x}"}\n',
        encoding="utf-8",
    )
    sources = load_sources(tmp_path / "sources.yaml")

    with pytest.raises(LoadError, match="not a plain identifier"):
        ensure_constraints(neo4j_session, sources)


@pytest.mark.integration
@pytest.mark.graph
def test_missing_data_file_says_what_to_run(neo4j_session, tmp_path):
    (tmp_path / "sources.yaml").write_text(SOURCES, encoding="utf-8")
    sources = load_sources(tmp_path / "sources.yaml")

    with pytest.raises(LoadError, match="backbone fetch"):
        load_backbone(neo4j_session, "_graphpack_test", sources, tmp_path / "data")


# ----------------------------------------------------------------------
# Deciding a property by vote
# ----------------------------------------------------------------------


def test_the_reading_most_mentions_agree_on_wins():
    """A property read out of prose is read many times and not always well. Plain
    MERGE is last-write-wins, so one bad sentence at the end of the corpus beats
    a hundred good ones before it."""
    from graphpack.backbone.load import _count_the_vote

    rows = _count_the_vote(
        {
            "kanun:6356": [
                {"title": "Sendikalar ve Toplu İş Sözleşmesi Kanun"},
                {"title": "Sendikalar ve Toplu İş Sözleşmesi Kanun"},
                {"title": "Kanun'un 2/3 hükmünde ... 4857 sayılı İş Kanun"},
            ]
        }
    )

    assert rows == [
        {"id": "kanun:6356", "props": {"title": "Sendikalar ve Toplu İş Sözleşmesi Kanun"}}
    ]


def test_a_tie_goes_to_the_earliest_mention():
    """Statutes are named in full the first time a decision cites them and by
    number afterwards, so the earliest reading is the likeliest whole name."""
    from graphpack.backbone.load import _count_the_vote

    rows = _count_the_vote({"k": [{"title": "Deniz İş Kanun"}, {"title": "Kanun ile 4857"}]})

    assert rows[0]["props"]["title"] == "Deniz İş Kanun"


def test_each_property_is_counted_on_its_own():
    """Two mentions of the same statute can each supply a different field, and a
    row that is missing one should not vote against it."""
    from graphpack.backbone.load import _count_the_vote

    rows = _count_the_vote(
        {"k": [{"title": "A", "kind": "law"}, {"title": "B"}, {"title": "A"}, {"kind": "code"}]}
    )

    assert rows[0]["props"] == {"title": "A", "kind": "law"}


def test_voting_is_off_unless_a_step_asks_for_it():
    """Most properties are stated once. Buffering every row costs memory, so a
    step opts in."""
    from graphpack.backbone.sources import LoadSpec

    assert LoadSpec(source="x.jsonl").vote is False


VOTING_SOURCES = textwrap.dedent(
    """\
    fetch:
      - id: things
        url: https://example.invalid/things.json
        out: things.jsonl

    load:
      - source: things.jsonl
        vote: true
        node:
          label: Thing
          id: "t:{name}"
          properties:
            title: "{title}"
    """
)


@pytest.mark.integration
@pytest.mark.graph
def test_a_voted_property_takes_the_reading_the_data_agrees_on(neo4j_session, pack_data):
    """End to end, from the `vote: true` in the YAML to the value in the graph.

    Three mentions of one thing, two agreeing. Without voting the last write
    wins and the node is named "Kanun ile 4857 sayili Kanun" — which is how
    statute 6356 came to be titled after statute 4857.
    """
    rows = [
        {"name": "a", "title": "Deniz Is Kanunu"},
        {"name": "a", "title": "Deniz Is Kanunu"},
        {"name": "a", "title": "Kanun ile 4857 sayili Kanun"},
    ]
    sources, data_dir = pack_data(VOTING_SOURCES, rows)
    pack = "_graphpack_test"
    neo4j_session.run("MATCH (n {pack: $p}) DETACH DELETE n", p=pack)

    try:
        load_backbone(neo4j_session, pack, sources, data_dir)

        row = neo4j_session.run(
            "MATCH (n:Thing {pack: $p}) RETURN n.title AS title, count(*) AS n", p=pack
        ).single()
        assert row["title"] == "Deniz Is Kanunu"
        assert row["n"] == 1, "three mentions of one identity are one node"
    finally:
        neo4j_session.run("MATCH (n {pack: $p}) DETACH DELETE n", p=pack)
