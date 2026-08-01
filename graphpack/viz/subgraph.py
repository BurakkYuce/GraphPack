"""Pull the part of the graph a run touched.

A trace records identifiers. Drawing it needs the nodes those identify and the
edges actually between them — the traversal knows it reached sixty packages from
urllib3, but not which of them depend on each other, and that structure is most
of what a picture is for.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Above this the picture stops being a picture. Sixty nodes is a readable
#: hairball; six hundred is a grey disc.
MAX_NODES = 120

_NODES = """
MATCH (n {pack: $pack}) WHERE n.id IN $ids
RETURN n.id AS id,
       toString(coalesce(n.name, n.title, n.case_number, n.number, n.slug, n.id)) AS label,
       [l IN labels(n) WHERE NOT l STARTS WITH '_'][0] AS kind
"""

#: Every edge among the nodes shown. Restricted to the set on purpose: an edge
#: to something not drawn is a line to nowhere.
_EDGES = """
MATCH (a {pack: $pack})-[r]->(b {pack: $pack})
WHERE a.id IN $ids AND b.id IN $ids
RETURN DISTINCT a.id AS start, type(r) AS type, b.id AS end
"""


@dataclass
class Subgraph:
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    #: Ids the trace named that the graph could not produce. Should be empty —
    #: the critique step catches these — but drawing an empty space where one
    #: was expected is more honest than dropping it silently.
    missing: list[str] = field(default_factory=list)

    @property
    def kinds(self) -> list[str]:
        seen: list[str] = []
        for node in self.nodes:
            if node["kind"] and node["kind"] not in seen:
                seen.append(node["kind"])
        return seen


def subgraph_for(
    session,
    pack: str,
    ids: list[str],
    limit: int = MAX_NODES,
    traversed: list[tuple[str, str, str]] | None = None,
) -> Subgraph:
    """Nodes for *ids*, the edges among them, and what the run derived.

    *traversed* is the trace's own edge list. Some of those edges are not in the
    database: "co-cited with 4857" is two `CITES` hops through a decision, and
    the run reports it as one `CO_CITED` edge. Drawing only stored edges left
    that question as twenty-six unconnected dots — a picture of the answer with
    the reasoning removed. They are drawn, and marked derived, because a
    relation the run computed is not a relation the graph holds.
    """
    wanted = list(dict.fromkeys(ids))[:limit]
    if len(ids) > limit:
        logger.info("Drawing %d of %d nodes — the rest would not be legible", limit, len(ids))
    if not wanted:
        return Subgraph()

    nodes = [dict(row) for row in session.run(_NODES, pack=pack, ids=wanted)]
    found = {n["id"] for n in nodes}
    edges = [dict(row) for row in session.run(_EDGES, pack=pack, ids=sorted(found))]

    _add_derived(edges, traversed or [], found)

    # Keep the trace's order: the first nodes are the ones the question named,
    # and the layout uses that to put them in the middle.
    order = {node_id: index for index, node_id in enumerate(wanted)}
    nodes.sort(key=lambda n: order.get(n["id"], len(order)))

    return Subgraph(nodes=nodes, edges=edges, missing=[i for i in wanted if i not in found])


def _add_derived(edges: list[dict], traversed: list[tuple[str, str, str]], drawn: set[str]) -> None:
    """Add the run's own relations, but only where the picture needs them.

    A derived edge earns its place when the stored edges cannot already show how
    the pair relates. "Co-cited with 4857" relates statutes that have nothing
    between them, so the line is the only thing connecting them. A blast radius
    reaches its packages *through* dependency edges that are already drawn, and
    laying sixty straight lines from the subject over that structure hides the
    paths the answer actually followed.
    """
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge["start"], set()).add(edge["end"])
        adjacency.setdefault(edge["end"], set()).add(edge["start"])

    for start, kind, end in traversed:
        if start not in drawn or end not in drawn or start == end:
            continue
        if _reaches(adjacency, start, end):
            continue
        edges.append({"start": start, "type": kind, "end": end, "derived": True})
        adjacency.setdefault(start, set()).add(end)
        adjacency.setdefault(end, set()).add(start)


def _reaches(adjacency: dict[str, set[str]], start: str, end: str) -> bool:
    """Whether the drawn edges already connect the two, in any direction.

    Undirected on purpose: the question is what a reader can trace with a
    finger, and a reader does not care which way the arrow points.
    """
    seen, queue = {start}, [start]
    while queue:
        node = queue.pop()
        if node == end:
            return True
        for nxt in adjacency.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return False
