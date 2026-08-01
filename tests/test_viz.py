"""The page a run produces.

Two properties matter here and neither is aesthetic. The page must be openable
from disk with nothing else present, and corpus text must not be able to end the
document it is embedded in — a statute title is data, and the graph carries it
verbatim into a `<script>` block.
"""

from __future__ import annotations

import json
import re

import pytest

from graphpack.agent.trace import Recorder, Trace
from graphpack.viz.render import PALETTE, render_page
from graphpack.viz.subgraph import Subgraph, subgraph_for

pytestmark = pytest.mark.unit


def a_trace(question: str = "what breaks if urllib3 breaks") -> Trace:
    recorder = Recorder(question=question, pack="oss")
    with recorder.step("lookup", tool="cypher") as event:
        event.node_ids = ["pypi:urllib3"]
        event.summary = "1 entity"
    with recorder.step("expand", tool="blast_radius") as event:
        event.node_ids = ["pypi:requests"]
        event.edge_ids = [("pypi:requests", "DEPENDS_ON", "pypi:urllib3")]
        event.summary = "1 reached"
    recorder.trace.answer = "requests"
    return recorder.trace


def a_subgraph(label: str = "urllib3") -> Subgraph:
    return Subgraph(
        nodes=[
            {"id": "pypi:urllib3", "label": label, "kind": "Package"},
            {"id": "pypi:requests", "label": "requests", "kind": "Package"},
        ],
        edges=[{"start": "pypi:requests", "type": "DEPENDS_ON", "end": "pypi:urllib3"}],
    )


def embedded_data(page: str) -> dict:
    """The payload the page will actually parse, read back out of the document."""
    match = re.search(r"const DATA = (.*?);\n", page, re.S)
    assert match, "the page should embed its data"
    return json.loads(match.group(1))


# ----------------------------------------------------------------------
# Self-containment — the whole reason it is one file
# ----------------------------------------------------------------------


def test_the_page_asks_the_network_for_nothing():
    """A CDN link is a page that is blank on a plane, and blank in two years when
    the CDN moves. Everything is inline or the file is not what it claims."""
    page = render_page(a_trace(), a_subgraph())

    assert not re.findall(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//""", page)


def test_the_page_is_a_whole_document():
    page = render_page(a_trace(), a_subgraph())

    assert page.startswith("<!doctype html>")
    assert "</html>" in page


# ----------------------------------------------------------------------
# Corpus text is data
# ----------------------------------------------------------------------


def test_a_label_cannot_close_the_script_block():
    """`</script>` inside a JSON string ends the block early: the rest of the
    graph is then parsed as HTML and the page renders blank. json.dumps does not
    escape it — this is the test that says we must."""
    page = render_page(a_trace(), a_subgraph(label="</script><h1>owned"))

    assert "</script><h1>" not in page
    assert embedded_data(page)["nodes"][0]["label"] == "</script><h1>owned"


def test_a_question_cannot_inject_markup_into_the_heading():
    page = render_page(a_trace(question="<img src=x onerror=alert(1)>"), a_subgraph())

    assert "<img src=x" not in page
    assert "&lt;img src=x" in page


def test_a_question_naming_a_placeholder_does_not_swallow_the_graph():
    """The heading and the data are substituted in one pass. Chained replaces
    would put the entire graph inside the <h1> for a question containing the
    literal placeholder."""
    page = render_page(a_trace(question="what is __DATA__"), a_subgraph())

    assert embedded_data(page)["trace"]["question"] == "what is __DATA__"
    assert page.count("const DATA = ") == 1


def test_a_line_separator_in_a_label_survives():
    """U+2028 is legal in JSON and a line break inside a JavaScript string
    literal — a syntax error rather than a bad label."""
    # Spelled out rather than pasted: an invisible character in a source file is
    # one a later edit drops without anybody seeing it go.
    label = "4857\u2028say\u0131l\u0131"
    page = render_page(a_trace(), a_subgraph(label=label))

    assert "\u2028" not in page
    assert embedded_data(page)["nodes"][0]["label"] == label


def test_turkish_text_is_carried_not_escaped():
    """ensure_ascii would work, but the point of a readable file is that it is
    readable."""
    page = render_page(a_trace(), a_subgraph(label="İş Kanunu"))

    assert "İş Kanunu" in page


# ----------------------------------------------------------------------
# What the page is given
# ----------------------------------------------------------------------


def test_each_kind_gets_its_own_colour():
    graph = Subgraph(
        nodes=[
            {"id": "a", "label": "a", "kind": "Decision"},
            {"id": "b", "label": "b", "kind": "Statute"},
            {"id": "c", "label": "c", "kind": "Decision"},
        ]
    )

    nodes = embedded_data(render_page(a_trace(), graph))["nodes"]

    assert nodes[0]["colour"] == nodes[2]["colour"] != nodes[1]["colour"]
    assert nodes[0]["colour"] in PALETTE


def test_the_trace_travels_with_the_page():
    """The replay is driven off the trace, so a page without it is a still
    picture that claims to be a recording."""
    payload = embedded_data(render_page(a_trace(), a_subgraph()))

    assert [e["step"] for e in payload["trace"]["events"]] == ["lookup", "expand"]
    assert payload["trace"]["events"][1]["edge_ids"] == [
        ["pypi:requests", "DEPENDS_ON", "pypi:urllib3"]
    ]


def test_an_empty_run_still_renders():
    """Nothing found is a legitimate answer — the unknown-statute question is one
    of the question set — and it is exactly when somebody wants to see the
    trace."""
    recorder = Recorder(question="9999 sayılı Kanun", pack="tr-law")
    with recorder.step("lookup") as event:
        event.summary = "no entity"

    page = render_page(recorder.trace, Subgraph())

    assert page.startswith("<!doctype html>")
    assert embedded_data(page)["nodes"] == []


# ----------------------------------------------------------------------
# Pulling the subgraph out of the graph
# ----------------------------------------------------------------------


class FakeSession:
    """Answers the two queries subgraph_for asks, from a fixed little graph."""

    def __init__(self, nodes, edges):
        self.nodes, self.edges = nodes, edges
        self.calls = []

    def run(self, query, **params):
        self.calls.append(params)
        if "labels(n)" in query:
            return [n for n in self.nodes if n["id"] in params["ids"]]
        wanted = set(params["ids"])
        return [e for e in self.edges if e["start"] in wanted and e["end"] in wanted]


def a_session(count: int = 4) -> FakeSession:
    nodes = [{"id": f"n{i}", "label": f"node {i}", "kind": "Package"} for i in range(count)]
    edges = [{"start": f"n{i}", "type": "DEPENDS_ON", "end": f"n{i + 1}"} for i in range(count - 1)]
    return FakeSession(nodes, edges)


def test_the_order_the_run_touched_them_in_is_kept():
    """Neo4j returns rows in whatever order it likes. The page seeds its layout
    along a spiral in list order, so the run's order is what puts the subject
    near the middle and its neighbours around it — sorted by id, the picture
    would be arranged by the alphabet."""
    graph = subgraph_for(a_session(), "oss", ["n2", "n0", "n1"])

    assert [n["id"] for n in graph.nodes] == ["n2", "n0", "n1"]


def test_only_edges_between_drawn_nodes_come_back():
    """An edge to something not on the page is a line to nowhere."""
    graph = subgraph_for(a_session(), "oss", ["n0", "n1"])

    assert graph.edges == [{"start": "n0", "type": "DEPENDS_ON", "end": "n1"}]


def test_an_id_the_graph_does_not_have_is_reported_not_dropped():
    """The critique step should have caught this. If one slips through, an empty
    space where a node was expected is more honest than a page that looks
    complete."""
    graph = subgraph_for(a_session(), "oss", ["n0", "ghost"])

    assert graph.missing == ["ghost"]
    assert [n["id"] for n in graph.nodes] == ["n0"]


def test_too_many_nodes_are_cut_to_a_number_that_can_be_read():
    session = a_session(count=30)

    graph = subgraph_for(session, "oss", [f"n{i}" for i in range(30)], limit=10)

    assert len(graph.nodes) == 10
    assert graph.missing == []


def test_a_repeated_id_is_asked_for_once():
    """nodes_touched is a path: the same node appears at several steps."""
    session = a_session()

    subgraph_for(session, "oss", ["n0", "n1", "n0"])

    assert session.calls[0]["ids"] == ["n0", "n1"]


def test_nothing_to_draw_asks_the_database_nothing():
    session = a_session()

    graph = subgraph_for(session, "oss", [])

    assert graph.nodes == [] and session.calls == []


def test_the_pack_is_passed_to_every_query():
    """Two packs share one database."""
    session = a_session()

    subgraph_for(session, "tr-law", ["n0", "n1"])

    assert all(call["pack"] == "tr-law" for call in session.calls)
    assert len(session.calls) == 2


# ----------------------------------------------------------------------
# Relations the run computed rather than read
# ----------------------------------------------------------------------


def test_a_relation_with_nothing_between_its_ends_is_drawn():
    """ "Co-cited with 4857" relates two statutes that have no edge between them
    — the two CITES hops go through a decision that is not in the result. Drawn
    only from the graph, the answer was twenty-six unconnected dots."""
    session = FakeSession(
        nodes=[{"id": i, "label": i, "kind": "Statute"} for i in ("k4857", "k6100")], edges=[]
    )

    graph = subgraph_for(
        session, "tr-law", ["k4857", "k6100"], traversed=[("k4857", "CO_CITED", "k6100")]
    )

    assert graph.edges == [{"start": "k4857", "type": "CO_CITED", "end": "k6100", "derived": True}]


def test_a_derived_edge_is_marked_as_one():
    """A relation the run computed is not a relation the graph holds, and the
    page draws the difference."""
    session = FakeSession(nodes=[{"id": i, "label": i, "kind": "S"} for i in "ab"], edges=[])

    graph = subgraph_for(session, "p", ["a", "b"], traversed=[("a", "CO_CITED", "b")])

    assert graph.edges[0]["derived"] is True


def test_no_derived_edge_where_the_stored_ones_already_connect():
    """A blast radius reaches its packages through dependency edges that are
    already on the page. Sixty straight lines from the subject would cover the
    paths the answer actually followed."""
    session = a_session(count=3)  # n0 -> n1 -> n2

    graph = subgraph_for(
        session, "oss", ["n0", "n1", "n2"], traversed=[("n0", "BLAST_RADIUS", "n2")]
    )

    assert not any(e.get("derived") for e in graph.edges)
    assert len(graph.edges) == 2


def test_connection_counts_whichever_way_the_arrows_point():
    """`a -> c <- b` already shows a reader how a and b relate."""
    session = FakeSession(
        nodes=[{"id": i, "label": i, "kind": "S"} for i in "abc"],
        edges=[
            {"start": "a", "type": "CITES", "end": "c"},
            {"start": "b", "type": "CITES", "end": "c"},
        ],
    )

    graph = subgraph_for(session, "p", ["a", "b", "c"], traversed=[("a", "CO_CITED", "b")])

    assert not any(e.get("derived") for e in graph.edges)


def test_a_relation_to_something_off_the_page_is_not_drawn():
    """The node cap cuts a large result. An edge to what was cut is a line to
    nowhere."""
    session = a_session(count=3)

    graph = subgraph_for(
        session, "oss", ["n0", "n1", "n2"], limit=2, traversed=[("n0", "CO_CITED", "n2")]
    )

    assert not any(e.get("derived") for e in graph.edges)


def test_the_page_says_which_lines_it_derived():
    graph = Subgraph(
        nodes=[{"id": i, "label": i, "kind": "Statute"} for i in ("a", "b")],
        edges=[{"start": "a", "type": "CO_CITED", "end": "b", "derived": True}],
    )

    page = render_page(a_trace(), graph)

    assert "not stored in the graph" in page
    assert embedded_data(page)["edges"][0]["derived"] is True


def test_the_note_about_dashes_is_behind_a_guard():
    """Whether the note appears is decided in the browser, which pytest cannot
    see. What it can check is that the decision is made at all: the text is in
    every page, and a condition stands in front of it."""
    page = render_page(a_trace(), a_subgraph())

    assert "edges.some(e => e.derived)" in page
