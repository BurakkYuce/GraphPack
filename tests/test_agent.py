"""The loop, its contract, and its trace.

Everything here runs without a model. The parts a model does — writing the reply,
choosing an intent when keywords do not — sit behind an optional argument
precisely so the rest is deterministic and can be pinned.
"""

from __future__ import annotations

import textwrap

import pytest

from graphpack.agent.contract import RetrievalError, load_retrieval_rules, render_cypher
from graphpack.agent.trace import Recorder

pytestmark = pytest.mark.unit

RULES = textwrap.dedent(
    """\
    lookup: |
      MATCH (n {pack: $pack}) WHERE n.id = $needle
      RETURN n.id AS id, n.name AS name LIMIT $limit

    intents:
      - name: blast_radius
        description: What breaks if this breaks.
        entity: PACKAGE
        match: ["break", "affected"]
        hops: 2
        limit: 30
        cypher: |
          MATCH (d:Package {pack: $pack})-[:DEPENDS_ON*1..{hops}]->(p {pack: $pack})
          WHERE p.id = $entity_id
          RETURN d.id AS id LIMIT {limit}
      - name: dependencies
        description: What this needs.
        entity: PACKAGE
        match: ["require", "needs"]
        cypher: |
          MATCH (p {pack: $pack})-[:DEPENDS_ON]->(d) WHERE p.id = $entity_id
          RETURN d.id AS id
    """
)


@pytest.fixture
def rules(tmp_path):
    def _make(body: str = RULES):
        (tmp_path / "retrieval.yaml").write_text(body, encoding="utf-8")
        return load_retrieval_rules(tmp_path / "retrieval.yaml")

    return _make


# ----------------------------------------------------------------------
# Routing
# ----------------------------------------------------------------------


def test_a_question_routes_to_the_intent_whose_words_it_uses(rules):
    loaded = rules()

    intent, score = loaded.route("what would break if urllib3 broke")

    assert intent.name == "blast_radius"
    assert score >= 1


def test_the_intent_matching_more_phrases_wins(rules):
    loaded = rules()

    intent, score = loaded.route("which packages are affected when this breaks")

    assert intent.name == "blast_radius"
    assert score == 2


def test_a_question_matching_nothing_routes_nowhere(rules):
    """The model is asked only here, which keeps the least predictable step off
    the common path."""
    intent, score = rules().route("tell me about python")

    assert intent is None
    assert score == 0


# ----------------------------------------------------------------------
# The Cypher contract
# ----------------------------------------------------------------------


def test_only_hops_and_limit_are_interpolated(rules):
    """Everything else is a $parameter the driver escapes. A template that built
    a query out of question text would be a different kind of program."""
    loaded = rules()
    rendered = render_cypher(loaded.intents[0].cypher, hops=2, limit=30)

    assert "*1..2]" in rendered
    assert "LIMIT 30" in rendered


def test_a_cypher_map_literal_survives_interpolation(rules):
    """`{pack: $pack}` is a Cypher map, not a placeholder. str.format reads it as
    a field named `pack` and dies on the format specifier — a mistake made twice
    in this codebase before the interpolation moved into one function."""
    rendered = render_cypher(rules().intents[0].cypher, hops=2, limit=30)

    assert "{pack: $pack}" in rendered


def test_an_unknown_placeholder_is_rejected(tmp_path):
    (tmp_path / "retrieval.yaml").write_text(
        "intents:\n  - {name: a, description: d, entity: E, "
        'cypher: "MATCH (n {pack: $pack}) RETURN n.id AS id LIMIT {rows}"}\n',
        encoding="utf-8",
    )

    with pytest.raises(RetrievalError, match="interpolated placeholder"):
        load_retrieval_rules(tmp_path / "retrieval.yaml")


def test_cypher_that_does_not_filter_by_pack_is_rejected(tmp_path):
    """Two packs share one database. A query without the filter reads the other
    one's graph and answers from it."""
    (tmp_path / "retrieval.yaml").write_text(
        'intents:\n  - {name: a, description: d, entity: E, cypher: "MATCH (n) RETURN n.id AS id"}\n',
        encoding="utf-8",
    )

    with pytest.raises(RetrievalError, match="does not filter on \\$pack"):
        load_retrieval_rules(tmp_path / "retrieval.yaml")


def test_cypher_returning_no_id_is_rejected(tmp_path):
    """The answer cites canonical identifiers and the critique step checks them
    against the graph. A row without one cannot be checked."""
    (tmp_path / "retrieval.yaml").write_text(
        "intents:\n  - {name: a, description: d, entity: E, "
        'cypher: "MATCH (n {pack: $pack}) RETURN n.name AS name"}\n',
        encoding="utf-8",
    )

    with pytest.raises(RetrievalError, match="must RETURN an 'id'"):
        load_retrieval_rules(tmp_path / "retrieval.yaml")


def test_duplicate_intent_names_are_rejected(tmp_path):
    body = (
        "intents:\n"
        '  - {name: a, description: d, entity: E, cypher: "MATCH (n {pack: $pack}) RETURN n.id AS id"}\n'
        '  - {name: a, description: e, entity: E, cypher: "MATCH (n {pack: $pack}) RETURN n.id AS id"}\n'
    )
    (tmp_path / "retrieval.yaml").write_text(body, encoding="utf-8")

    with pytest.raises(RetrievalError, match="duplicate intent name"):
        load_retrieval_rules(tmp_path / "retrieval.yaml")


# ----------------------------------------------------------------------
# The trace
# ----------------------------------------------------------------------


def test_a_step_records_what_it_touched_and_how_long_it_took():
    recorder = Recorder(question="q", pack="p")

    with recorder.step("lookup", tool="cypher") as event:
        event.node_ids = ["a", "b"]
        event.summary = "found two"

    assert recorder.trace.steps == ["lookup"]
    assert recorder.trace.events[0].node_ids == ["a", "b"]
    assert recorder.trace.events[0].duration_ms >= 0


def test_a_step_that_raises_still_leaves_a_record():
    """A run that failed halfway is exactly when the trace is worth having."""
    recorder = Recorder(question="q", pack="p")

    with pytest.raises(ValueError):
        with recorder.step("expand"):
            raise ValueError("the query was wrong")

    assert recorder.trace.steps == ["expand"]
    assert "the query was wrong" in recorder.trace.events[0].detail["error"]


def test_an_unknown_step_name_is_refused():
    """The replay styles steps by name and tests assert on the sequence; a typo
    would silently become a step nobody draws."""
    with pytest.raises(ValueError, match="unknown step"):
        Recorder(question="q", pack="p").step("wander")


def test_nodes_touched_is_a_path_not_a_set():
    """Order is what the replay animates."""
    recorder = Recorder(question="q", pack="p")
    with recorder.step("lookup") as event:
        event.node_ids = ["a"]
    with recorder.step("expand") as event:
        event.node_ids = ["b", "a", "c"]

    assert recorder.trace.nodes_touched == ["a", "b", "c"]


def test_a_trace_serialises_whole():
    import json

    recorder = Recorder(question="what breaks?", pack="oss")
    with recorder.step("expand", tool="blast_radius") as event:
        event.node_ids = ["pypi:requests"]
        event.edge_ids = [("pypi:requests", "DEPENDS_ON", "pypi:urllib3")]
    recorder.trace.answer = "requests"

    payload = json.loads(recorder.trace.to_json())

    assert payload["question"] == "what breaks?"
    assert payload["events"][0]["edge_ids"] == [["pypi:requests", "DEPENDS_ON", "pypi:urllib3"]]


# ----------------------------------------------------------------------
# The real packs
# ----------------------------------------------------------------------


@pytest.mark.parametrize("pack_name", ["oss", "tr-law"])
def test_every_pack_declares_usable_intents(pack_name):
    from graphpack.packs import load_pack

    pack = load_pack(pack_name)
    loaded = load_retrieval_rules(pack.path("retrieval.yaml"))

    assert loaded.intents
    assert loaded.lookup, "a pack should say how a name in a question becomes a node"


@pytest.mark.parametrize("pack_name", ["oss", "tr-law"])
def test_every_question_routes_to_the_intent_it_claims(pack_name):
    """The question set doubles as the routing test: each question names the
    intent it should reach, so a phrase list that stops covering its questions
    fails here rather than in a run."""
    from graphpack.agent.runner import load_questions
    from graphpack.packs import load_pack

    pack = load_pack(pack_name)
    rules = load_retrieval_rules(pack.path("retrieval.yaml"))

    wrong = []
    for question in load_questions(pack.path("questions.jsonl")):
        intent, _ = rules.route(question.question)
        if question.intent and (intent is None or intent.name != question.intent):
            wrong.append(
                f"{question.id}: wanted {question.intent}, got {intent.name if intent else 'nothing'}"
            )

    assert not wrong, "\n".join(wrong)
